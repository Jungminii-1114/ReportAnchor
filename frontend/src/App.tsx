import { useEffect, useRef, useState } from 'react';
import { FileText, FolderOpen, MessageSquare, X } from 'lucide-react';
import { api, apiErrorText, isAbort, pollJob } from './api';
import type { UploadResult } from './api';
import { maxDocumentScope, threadLabels } from './contracts';
import type { DocumentItem, EvidenceLimit, FinalReview, ModelConfig, QueryMode, SourceReference } from './contracts';
import { IconButton } from './Controls';
import { Explorer } from './Explorer';
import { Reader } from './Reader';
import type { ReaderLocation } from './Reader';
import { ReviewPanel } from './ReviewPanel';
import type { Turn, UploadProgress } from './ReviewPanel';
import { ModelDialog, RenameDialog, RestoreDialog } from './WorkspaceDialogs';

type Pane = 'explorer' | 'reader' | 'review';
type WorkspaceDialog = { kind: 'settings' | 'restore' } | { kind: 'rename'; item: DocumentItem };

function reviewMatches(review: FinalReview | null | undefined, requestRef: string, mode: QueryMode): review is FinalReview {
  return Boolean(review && review.request_ref === requestRef && review.mode === mode &&
    Object.hasOwn(threadLabels, review.thread_status) &&
    Array.isArray(review.verified_claims) && Array.isArray(review.unconfirmed_items) && Array.isArray(review.sources));
}

export default function App() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [model, setModel] = useState<ModelConfig>({ provider: 'local', model: null });
  const [connection, setConnection] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [location, setLocation] = useState<ReaderLocation | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState(false);
  const [pane, setPane] = useState<Pane>('reader');
  const [highlight, setHighlight] = useState(true);
  const [mode, setMode] = useState<QueryMode>('qa');
  const [limit, setLimit] = useState<EvidenceLimit>(3);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState(false);
  const [upload, setUpload] = useState<UploadProgress | null>(null);
  const [notice, setNotice] = useState('');
  const [dialog, setDialog] = useState<WorkspaceDialog | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadRequest = useRef<AbortController | null>(null);
  const queryRequest = useRef<AbortController | null>(null);
  const lastPages = useRef(new Map<string, number>());
  const document = documents.find((item) => item.document_id === location?.documentId);
  const scope = documents.filter((item) => item.active && selected.has(item.document_id));

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    Promise.all([api.health(signal), api.documents(signal)]).then(([, response]) => {
      if (signal.aborted) return;
      const active = response.documents.filter((item) => item.active);
      setDocuments(response.documents);
      setModel(response.model);
      const initialScope = active.length > maxDocumentScope ? active.slice(0, 1) : active;
      setSelected(new Set(initialScope.map((item) => item.document_id)));
      setLocation(active[0] ? { documentId: active[0].document_id, page: 1 } : null);
      setConnection('ready');
    }).catch(() => { if (!signal.aborted) setConnection('failed'); });
    return () => controller.abort();
  }, [connectionAttempt]);

  useEffect(() => () => {
    uploadRequest.current?.abort();
    queryRequest.current?.abort();
  }, []);

  function navigate(next: ReaderLocation) {
    lastPages.current.set(next.documentId, next.page);
    setLocation(next);
    setPane('reader');
  }

  function open(item: DocumentItem) {
    if (location?.documentId === item.document_id) {
      setPane('reader');
      return;
    }
    navigate({ documentId: item.document_id, page: Math.min(lastPages.current.get(item.document_id) ?? 1, Math.max(1, item.page_count)) });
  }

  function jump(source: SourceReference, page: number, hasHighlight = true) {
    const item = documents.find((entry) => entry.document_id === source.document_id);
    if (!item || page > item.page_count ||
      (item.document_version && source.document_version && item.document_version !== source.document_version)) {
      setNotice('인용된 문서 버전의 지면을 열 수 없습니다.');
      return;
    }
    setHighlight(true);
    navigate({ documentId: source.document_id, page, chunkId: source.chunk_id, highlightAvailable: hasHighlight });
  }

  function select(id: string, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function updateDocument(id: string, patch: { title?: string; active?: boolean }) {
    const updated = await api.updateDocument(id, patch);
    setDocuments((current) => current.map((item) => item.document_id === id ? updated : item));
    if (patch.active === false) {
      select(id, false);
      setLocation((current) => {
        if (current?.documentId !== id) return current;
        const next = documents.find((item) => item.active && item.document_id !== id);
        return next ? { documentId: next.document_id, page: lastPages.current.get(next.document_id) ?? 1 } : null;
      });
    }
  }

  function chooseFiles() {
    if (connection === 'ready') fileInput.current?.click();
    else setNotice('서버에 연결한 뒤 자료를 추가하세요.');
  }

  async function addFiles(files: File[]) {
    if (!files.length || uploadRequest.current) return;
    if (connection !== 'ready') { setNotice('서버에 연결한 뒤 자료를 추가하세요.'); return; }
    const pdfs = files.filter((file) => file.type === 'application/pdf' || /\.pdf$/i.test(file.name));
    if (!pdfs.length) { setNotice('PDF 파일을 추가하세요.'); return; }
    setNotice('');
    const controller = new AbortController();
    const { signal } = controller;
    uploadRequest.current = controller;
    const known = new Set(documents.map((item) => item.document_id));
    const added = new Set<string>();
    const errors: string[] = [];
    if (pdfs.length < files.length) errors.push('PDF 형식의 파일만 추가할 수 있습니다.');

    try {
      for (const [index, file] of pdfs.entries()) {
        signal.throwIfAborted();
        const position = { current: index + 1, total: pdfs.length };
        setUpload(position);
        try {
          const { job_id } = await api.upload(file, signal);
          const result = await pollJob<UploadResult>(job_id, signal, (job) => {
            if (typeof job.progress === 'number' && Number.isFinite(job.progress)) {
              setUpload({ ...position, progress: Math.min(100, Math.max(0, job.progress)) });
            }
          });
          if (result?.document_id) added.add(result.document_id);
        } catch (error) {
          if (signal.aborted || isAbort(error)) throw error;
          errors.push(apiErrorText(error, `${file.name}을(를) 추가하지 못했습니다.`));
        }
      }
      const response = await api.documents(signal);
      if (signal.aborted) return;
      const newlyAvailable = response.documents.filter((item) => item.active && (added.has(item.document_id) || !known.has(item.document_id)));
      setDocuments(response.documents);
      setSelected((current) => {
        const next = new Set(response.documents.filter((item) => item.active && current.has(item.document_id)).map((item) => item.document_id));
        for (const item of newlyAvailable) next.add(item.document_id);
        return next;
      });
      if (newlyAvailable[0]) open(newlyAvailable[0]);
    } catch (error) {
      if (!signal.aborted) errors.push(apiErrorText(error, '자료 목록을 새로 불러오지 못했습니다.'));
    } finally {
      if (!signal.aborted) {
        setUpload(null);
        if (errors.length) setNotice([...new Set(errors)].join(' '));
      }
      if (uploadRequest.current === controller) uploadRequest.current = null;
    }
  }

  function submit(question: string) {
    if (!scope.length || scope.length > maxDocumentScope || connection !== 'ready' || queryRequest.current) return false;
    const controller = new AbortController();
    const { signal } = controller;
    queryRequest.current = controller;
    const id = crypto.randomUUID();
    setTurns((current) => [...current, { id, question, mode }]);
    setPending(true);
    const request = { question, document_ids: scope.map((item) => item.document_id), mode, top_k: limit, request_ref: id };

    function finish(result: Partial<Turn>) {
      setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, ...result } : turn));
    }
    api.query(request, signal).then(({ job_id }) => pollJob<FinalReview>(job_id, signal)).then((review) => {
      if (signal.aborted) return;
      if (!reviewMatches(review, id, mode)) throw new Error('Invalid review');
      finish({ review });
    }).catch((error: unknown) => {
      if (queryRequest.current !== controller) return;
      if (signal.aborted || isAbort(error)) finish({ cancelled: true });
      else finish({ error: apiErrorText(error, '질문을 처리하지 못했습니다. 다시 시도하세요.') });
    }).finally(() => {
      if (queryRequest.current === controller) {
        queryRequest.current = null;
        setPending(false);
      }
    });
    return true;
  }

  function newQuestion() {
    queryRequest.current?.abort();
    queryRequest.current = null;
    setPending(false);
    setTurns([]);
  }

  return <div className={`workspace ${collapsed ? 'explorer-collapsed' : ''}`} data-pane={pane}>
    <input type="file" ref={fileInput} hidden accept="application/pdf,.pdf" multiple onChange={(event) => {
      void addFiles(Array.from(event.target.files ?? []));
      event.target.value = '';
    }} />
    <Explorer collapsed={collapsed} documents={documents} openId={location?.documentId ?? null} selected={selected}
      uploading={upload !== null} onCollapse={setCollapsed} onPick={chooseFiles} onUpload={(files) => void addFiles(files)}
      onOpen={open} onSelect={select} onRename={(item) => setDialog({ kind: 'rename', item })}
      onArchive={(item) => { void updateDocument(item.document_id, { active: false }).catch(() => setNotice('자료를 제외하지 못했습니다.')); }}
      onRestore={() => setDialog({ kind: 'restore' })} onSettings={() => setDialog({ kind: 'settings' })} />
    <Reader document={document} location={location} highlight={highlight} uploading={upload !== null} onPick={chooseFiles}
      onHighlight={() => setHighlight(!highlight)} onPage={(page) => {
        if (location) navigate({ documentId: location.documentId, page });
      }} />
    <ReviewPanel documents={documents} turns={turns} location={location} mode={mode} limit={limit} upload={upload}
      pending={pending} connectionReady={connection === 'ready'} selectedCount={scope.length} onMode={setMode} onLimit={setLimit}
      onCancel={() => queryRequest.current?.abort()} onNew={newQuestion} onSubmit={submit} onJump={jump} />
    <nav className="compact-nav" aria-label="워크스페이스">
      <button aria-pressed={pane === 'explorer'} onClick={() => { setCollapsed(false); setPane('explorer'); }}><FolderOpen size={18} />자료</button>
      <button aria-pressed={pane === 'reader'} onClick={() => setPane('reader')}><FileText size={18} />지면</button>
      <button aria-pressed={pane === 'review'} onClick={() => setPane('review')}><MessageSquare size={18} />대화</button>
    </nav>
    {(notice || connection === 'failed' || (upload && pane !== 'review')) && <div className="workspace-notice"
      data-upload-only={!notice && connection !== 'failed' ? 'true' : undefined}>
      {notice ? <><p role="alert">{notice}</p><IconButton label="알림 닫기" onClick={() => setNotice('')}><X size={18} /></IconButton></> :
        connection === 'failed' ? <><p role="status">서버에 연결하지 못했습니다.</p>
          <button className="text-button" onClick={() => { setConnection('loading'); setConnectionAttempt(connectionAttempt + 1); }}>다시 연결</button></> :
          <><p role="status">자료 추가 중{upload?.progress !== undefined ? ` · ${Math.round(upload.progress)}%` : '…'}</p>
            <button className="text-button" onClick={() => setPane('review')}>진행 보기</button></>}
    </div>}
    {dialog?.kind === 'settings' && <ModelDialog config={model} onClose={() => setDialog(null)} onSave={async (config) => {
      setModel(await api.model(config));
    }} />}
    {dialog?.kind === 'rename' && <RenameDialog item={dialog.item} onClose={() => setDialog(null)}
      onSave={(title) => updateDocument(dialog.item.document_id, { title })} />}
    {dialog?.kind === 'restore' && <RestoreDialog items={documents.filter((item) => !item.active)} onClose={() => setDialog(null)}
      onRestore={(item) => updateDocument(item.document_id, { active: true })} />}
  </div>;
}
