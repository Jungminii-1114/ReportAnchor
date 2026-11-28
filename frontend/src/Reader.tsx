import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, FileText, Focus, Info, Maximize2, Plus } from 'lucide-react';
import { api, isAbort } from './api';
import { Dialog, IconButton, Popup } from './Controls';
import type { DocumentItem } from './contracts';
import { PdfViewer } from './PdfViewer';
import type { PdfViewerProps } from './PdfViewer';

export interface ReaderLocation {
  documentId: string;
  page: number;
  chunkId?: string;
  highlightAvailable?: boolean;
}

interface ReaderProps {
  document: DocumentItem | undefined;
  location: ReaderLocation | null;
  highlight: boolean;
  uploading: boolean;
  onHighlight: () => void;
  onPick: () => void;
  onPage: (page: number) => void;
}

type ReaderDialog = 'text' | 'info' | 'zoom';

function PageField({ page, count, onPage }: { page: number; count: number; onPage: (page: number) => void }) {
  const [draft, setDraft] = useState<{ page: number; value: string } | null>(null);
  const input = draft?.page === page ? draft.value : String(page);
  function commit() {
    setDraft(null);
    const value = Number(input);
    if (/^\d+$/.test(input) && value >= 1 && value <= count && value !== page) onPage(value);
  }

  return <form className="page-field" onSubmit={(event) => { event.preventDefault(); commit(); }}>
    <span aria-hidden="true">p.</span>
    <input aria-label={`페이지 번호, 총 ${count}쪽`} type="text" inputMode="numeric" value={input}
      maxLength={String(count).length} style={{ width: `${Math.max(2, String(count).length)}ch` }}
      onChange={(event) => setDraft({ page, value: event.target.value })}
      onFocus={(event) => event.target.select()} onBlur={commit} />
    <span aria-hidden="true">/ {count}</span>
  </form>;
}

function DocumentDialog({ kind, item, viewer, onClose }: {
  kind: 'text' | 'info';
  item: DocumentItem;
  viewer: PdfViewerProps;
  onClose: () => void;
}) {
  const [result, setResult] = useState<{ text?: string; filename?: string; version?: string; count?: number }>();
  const [failed, setFailed] = useState(false);
  const { documentId, page, chunkId } = viewer;

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    async function load() {
      if (kind === 'text' && chunkId) {
        const chunk = await api.chunk(documentId, chunkId, signal);
        if (!signal.aborted) setResult({ text: chunk.context_text ?? chunk.raw_content ?? chunk.text ?? chunk.quote });
      } else if (kind === 'text') {
        const pageText = await api.pageText(documentId, page, signal);
        if (!signal.aborted) setResult({ text: pageText.text });
      } else {
        const detail = await api.document(documentId, signal);
        if (!signal.aborted) setResult({
          text: detail.pages?.find((entry) => entry.page === page)?.text ?? detail.text,
          filename: detail.filename,
          version: detail.document_version,
          count: detail.page_count,
        });
      }
    }
    load().catch((error: unknown) => { if (!isAbort(error) && !signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [kind, documentId, page, chunkId]);

  return <Dialog title={kind === 'text' ? '원문 텍스트' : '문서 정보'} wide={kind === 'text'} onClose={onClose}>
    <div className="dialog-body">
      {failed ? <p role="alert">문서 정보를 불러오지 못했습니다.</p> : !result ? <p role="status">불러오는 중…</p> :
        kind === 'text' ? <div className="original-text">{result.text?.trim() || (chunkId ?
          '표시할 원문 텍스트가 없습니다.' : '대화에서 근거를 선택하면 원문 구간을 볼 수 있습니다.')}</div> :
          <dl className="document-info">
            <dt>이름</dt><dd>{item.title}</dd>
            <dt>페이지</dt><dd>{result.count ?? item.page_count}쪽</dd>
            {result.filename && <><dt>파일</dt><dd>{result.filename}</dd></>}
            {result.version && <><dt>버전</dt><dd className="meta">{result.version}</dd></>}
          </dl>}
    </div>
  </Dialog>;
}

export function Reader({ document: item, location, highlight, uploading, onHighlight, onPick, onPage }: ReaderProps) {
  const [dialog, setDialog] = useState<ReaderDialog | null>(null);
  const desk = useRef<HTMLDivElement>(null);
  const viewer = item && location ? { ...location, highlight: highlight && location.highlightAvailable !== false } : null;

  useLayoutEffect(() => {
    desk.current?.scrollTo({ top: 0, left: 0 });
  }, [location?.documentId, location?.page]);

  return <main className="reader" aria-label="문서 지면">
    <header className="pane-header reader-header">
      {item && <div className="document-tab" title={item.title}><span>{item.title}</span></div>}
      {viewer && <Popup label="문서 메뉴">{(close) => <>
        <button onClick={() => { close(); setDialog('text'); }}><FileText size={17} />원문 텍스트 보기</button>
        <button onClick={() => { close(); onHighlight(); }}><Focus size={17} />근거 하이라이트 {highlight ? '끄기' : '켜기'}</button>
        <button onClick={() => { close(); setDialog('zoom'); }}><Maximize2 size={17} />크게 보기</button>
        <button onClick={() => { close(); setDialog('info'); }}><Info size={17} />문서 정보</button>
      </>}</Popup>}
    </header>
    <div className={`desk ${viewer ? '' : 'desk-empty'}`} ref={desk}>
      {viewer ? <div className="paper"><PdfViewer {...viewer} /></div> : <div className="reader-empty">
        <button className="primary-button" disabled={uploading} onClick={onPick}><Plus size={18} />자료 추가</button>
        <p>PDF를 추가하거나 탐색기에서 자료를 여세요.</p>
      </div>}
    </div>
    {item && viewer && <div className="reader-bar" role="group" aria-label="페이지 이동">
      <IconButton label="이전 페이지" disabled={viewer.page <= 1} onClick={() => onPage(viewer.page - 1)}>
        <ChevronLeft size={18} />
      </IconButton>
      <PageField key={viewer.documentId} page={viewer.page} count={item.page_count} onPage={onPage} />
      <IconButton label="다음 페이지" disabled={viewer.page >= item.page_count} onClick={() => onPage(viewer.page + 1)}>
        <ChevronRight size={18} />
      </IconButton>
      <IconButton className="reader-zoom" label="페이지 크게 보기" onClick={() => setDialog('zoom')}>
        <Maximize2 size={17} />
      </IconButton>
    </div>}
    {item && viewer && dialog === 'zoom' && <Dialog title="크게 보기" wide onClose={() => setDialog(null)}>
      <div className="zoom-page"><PdfViewer {...viewer} /></div>
    </Dialog>}
    {item && viewer && dialog && dialog !== 'zoom' &&
      <DocumentDialog key={`${viewer.documentId}:${viewer.page}:${viewer.chunkId ?? ''}:${dialog}`} kind={dialog}
        item={item} viewer={viewer} onClose={() => setDialog(null)} />}
  </main>;
}
