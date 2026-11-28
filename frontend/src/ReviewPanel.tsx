import { useEffect, useId, useRef, useState } from 'react';
import { ArrowUp, ChevronDown, ChevronUp, FileText, Plus } from 'lucide-react';
import { api, isAbort } from './api';
import { AnchorTile, IconButton, Popup } from './Controls';
import { maxDocumentScope, threadLabels } from './contracts';
import type { ChunkDetail, DocumentItem, EvidenceLimit, FinalReview, QueryMode, SourceReference } from './contracts';
import type { ReaderLocation } from './Reader';

export interface Turn {
  id: string;
  question: string;
  mode: QueryMode;
  review?: FinalReview;
  error?: string;
  cancelled?: boolean;
}

export interface UploadProgress { current: number; total: number; progress?: number }

function sourcePages(source: SourceReference, includeExpanded = false) {
  const spans = includeExpanded ? [...(source.source_refs ?? []), ...(source.expanded_source_refs ?? [])] : source.source_refs ?? [];
  const pages = [source.page ?? source.pdf_page, ...(source.pages ?? []), ...spans.map((span) => span.page ?? span.pdf_page)];
  if (includeExpanded) pages.push(...(source.expanded_pages ?? []));
  return [...new Set(pages.filter((page): page is number => typeof page === 'number' && Number.isInteger(page) && page > 0))];
}

function sourceKey(source: SourceReference) {
  return `${source.document_id}:${source.document_version ?? ''}:${source.chunk_id}`;
}

function SourceText({ source }: { source: SourceReference }) {
  const [chunk, setChunk] = useState<ChunkDetail>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api.chunk(source.document_id, source.chunk_id, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (result.document_version && source.document_version && result.document_version !== source.document_version) setFailed(true);
      else setChunk(result);
    }).catch((error: unknown) => { if (!isAbort(error) && !controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [source.document_id, source.chunk_id, source.document_version]);

  const spans = [
    ...(chunk?.source_refs ?? source.source_refs ?? []), ...(chunk?.expanded_source_refs ?? source.expanded_source_refs ?? []),
  ];
  const quote = chunk?.context_text || chunk?.raw_content || chunk?.text || chunk?.quote || source.quote?.trim() ||
    spans.map((span) => span.text).filter(Boolean).join('\n') || source.excerpt?.trim();
  const table = source.table_context ?? chunk?.table_context;
  return <div className="source-text">
    {table && <dl className="table-context">
      {table.table_title && <><dt>표</dt><dd>{table.table_title}</dd></>}
      {table.row_headers?.length ? <><dt>행</dt><dd>{table.row_headers.join(' · ')}</dd></> : null}
      {table.column_headers?.length ? <><dt>열</dt><dd>{table.column_headers.join(' · ')}</dd></> : null}
      {table.unit && <><dt>단위</dt><dd>{table.unit}</dd></>}
    </dl>}
    {quote ? <blockquote className="original-text">{quote}</blockquote> :
      <p role="status">{failed ? '원문을 불러오지 못했습니다.' : chunk ? '표시할 원문 텍스트가 없습니다.' : '불러오는 중…'}</p>}
  </div>;
}

function SourceRow({ source, title, onJump }: {
  source: SourceReference;
  title: string | undefined;
  onJump: (source: SourceReference, page: number, hasHighlight?: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const pages = sourcePages(source, true);
  const primaryPages = sourcePages(source);
  const titleText = title ?? '원문 구간';
  return <li className="source-detail">
    <div className="source-detail-row">
      <button type="button" className="source-disclosure" aria-expanded={open} aria-controls={id}
        onClick={() => setOpen(!open)}>
        <FileText size={15} aria-hidden="true" /><span className="truncate">{titleText}</span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      <div className="source-detail-pages">
        {pages.map((page) => <button type="button" key={page} className="page-link meta"
          aria-label={`${titleText} ${page}쪽으로 이동`} onClick={() => onJump(source, page, primaryPages.includes(page))}>p.{page}</button>)}
      </div>
    </div>
    <div id={id} hidden={!open}>{open && <SourceText source={source} />}</div>
  </li>;
}

function Answer({ review, documents, location, onJump }: {
  review: FinalReview;
  documents: DocumentItem[];
  location: ReaderLocation | null;
  onJump: (source: SourceReference, page: number, hasHighlight?: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const sources = [...new Map(review.sources.map((source) => [sourceKey(source), source])).values()];
  const titleOf = (id: string) => documents.find((item) => item.document_id === id)?.title;
  const locations = new Map<string, { source: SourceReference; page: number }>();
  for (const source of sources) {
    for (const page of sourcePages(source)) {
      const key = JSON.stringify([source.document_id, source.document_version, page]);
      if (!locations.has(key)) locations.set(key, { source, page });
    }
  }
  const pageLinks = [...locations.values()].sort((a, b) => {
    const isCurrent = ({ source, page }: typeof a) => source.document_id === location?.documentId && page === location.page;
    return Number(isCurrent(b)) - Number(isCurrent(a));
  });
  const claims = review.verified_claims;
  const unconfirmed = review.unconfirmed_items;
  const evidenceCount = sources.length || claims.length + unconfirmed.length;
  const fallback = {
    complete: '답변이 완료되었습니다.', partial: '일부 항목을 확인했습니다.', insufficient: '확인할 근거를 찾지 못했습니다.',
    clarification: '질문을 조금 더 구체적으로 입력하세요.', unsupported: '이 요청은 지원하지 않습니다.', error: '질문을 처리하지 못했습니다.',
  }[review.thread_status];
  const summary = (review.thread_status === 'error' ? review.error_message?.trim() : undefined) || review.summary_text?.trim();
  const showClaims = (review.thread_status === 'complete' || review.thread_status === 'partial') && claims.length > 0;

  return <div className="answer">
    <div className="answer-header"><AnchorTile /><span className="speaker">앵커</span>
      <span className={`thread-status status-${review.thread_status}`} role="status"><span className="status-dot" aria-hidden="true" />
        {threadLabels[review.thread_status]}</span>
    </div>
    {summary ? <p className="answer-text">{summary}</p> : showClaims ?
      <ul className="answer-claims">{claims.map((claim) => <li key={claim.claim_id}>{claim.claim_text}</li>)}</ul> :
      <p className="answer-text">{fallback}</p>}
    {review.evidence_incomplete && <p className="evidence-notice">
      {review.evidence_notice?.trim() || '일부 자료에서 근거를 확인하지 못했습니다.'}
    </p>}
    {pageLinks.length > 0 && <div className="page-links" aria-label="근거 지면 이동">
      <span className="page-links-label"><FileText size={14} aria-hidden="true" />근거</span>
      {pageLinks.map(({ source, page }, index) => <span className="page-link-group"
        key={JSON.stringify([source.document_id, source.document_version, page])}>
        {index > 0 && <span aria-hidden="true">·</span>}
        <button type="button" className="page-link meta" onClick={() => onJump(source, page)}
          aria-label={`${titleOf(source.document_id) ?? source.document_title ?? '근거 문서'} ${page}쪽으로 이동`}>p.{page}</button>
      </span>)}
    </div>}
    {evidenceCount > 0 && <>
      <button type="button" className="evidence-toggle" aria-expanded={expanded} aria-controls={detailId}
        onClick={() => setExpanded(!expanded)}>
        {expanded ? <>근거 접기 <ChevronUp size={15} /></> : <>근거 {evidenceCount}개 보기 <ChevronDown size={15} /></>}
      </button>
      <div className="evidence-detail" id={detailId} hidden={!expanded}>
        {expanded && <>
          {(claims.length > 0 || unconfirmed.length > 0) && <ul className="claim-list" aria-label="주장별 검증 결과">
            {claims.map((claim) => <li key={claim.claim_id}>
              <span className="claim-chip chip-supported">뒷받침</span><span>{claim.claim_text}</span>
            </li>)}
            {unconfirmed.map((item, index) => {
              const contradicted = (item.status ?? item.verdict) === 'contradicted';
              return <li key={`${item.claim_id ?? 'unconfirmed'}:${index}`}>
                <span className={`claim-chip ${contradicted ? 'chip-contradicted' : 'chip-insufficient'}`}>
                  {contradicted ? '모순' : '근거 부족'}</span>
                <span>{(contradicted ? item.reason : item.claim_text || item.reason) ||
                  (contradicted ? '원문과 일치하지 않습니다.' : '확인할 근거를 찾지 못했습니다.')}
                </span>
              </li>;
            })}
          </ul>}
          {sources.length > 0 && <ul className="source-list" aria-label="출처 구간">
            {sources.map((source) => <SourceRow key={sourceKey(source)} source={source}
              title={titleOf(source.document_id) ?? source.document_title} onJump={onJump} />)}
          </ul>}
        </>}
      </div>
    </>}
  </div>;
}

interface ReviewPanelProps {
  turns: Turn[];
  documents: DocumentItem[];
  location: ReaderLocation | null;
  mode: QueryMode;
  limit: EvidenceLimit;
  connectionReady: boolean;
  selectedCount: number;
  pending: boolean;
  upload: UploadProgress | null;
  onCancel: () => void;
  onNew: () => void;
  onMode: (mode: QueryMode) => void;
  onLimit: (limit: EvidenceLimit) => void;
  onSubmit: (question: string) => boolean;
  onJump: (source: SourceReference, page: number, hasHighlight?: boolean) => void;
}

export function ReviewPanel(props: ReviewPanelProps) {
  const [question, setQuestion] = useState('');
  const input = useRef<HTMLTextAreaElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const shouldFollow = useRef(true);
  const hintId = useId();
  const constraint = !props.connectionReady ? '서버에 연결한 뒤 질문할 수 있습니다.' :
    props.selectedCount === 0 ? '검색할 자료를 선택하세요.' :
    props.selectedCount > maxDocumentScope ? `검색할 자료는 ${maxDocumentScope}개까지 선택할 수 있습니다.` : '';
  const canSend = !props.pending && !constraint && Boolean(question.trim());

  useEffect(() => { if (shouldFollow.current) end.current?.scrollIntoView({ block: 'end' }); }, [props.turns]);

  function submit() {
    if (!canSend) return;
    shouldFollow.current = true;
    if (props.onSubmit(question.trim())) setQuestion('');
  }

  return <aside className="review-panel" aria-label="검토 대화">
    <header className="pane-header review-header">
      <h1>대화</h1>
      <Popup label="대화 메뉴">{(close) => <>
        <button disabled={props.turns.length === 0 && !question} onClick={() => {
          close(); props.onNew(); setQuestion(''); input.current?.focus();
        }}><Plus size={17} />새 질문</button>
        <fieldset><legend>대화 모드</legend>
          <label><input type="radio" name="query-mode" checked={props.mode === 'qa'} onChange={() => props.onMode('qa')} />질의·검증</label>
          <label><input type="radio" name="query-mode" checked={props.mode === 'search'} onChange={() => props.onMode('search')} />검색만</label>
        </fieldset>
        <fieldset className="evidence-limit"><legend>근거 수</legend>
          {([3, 5, 10] as const).map((limit) => <label key={limit}>
            <input type="radio" name="evidence-limit" checked={props.limit === limit} onChange={() => props.onLimit(limit)} />{limit}
          </label>)}
        </fieldset>
      </>}</Popup>
    </header>
    <div className="thread" ref={thread} onScroll={() => {
      const node = thread.current;
      if (node) shouldFollow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
    }}>
      {props.turns.map((turn) => <article className="turn" key={turn.id}>
        <p className="question-bubble">{turn.question}</p>
        {turn.review ? <Answer review={turn.review} documents={props.documents} location={props.location} onJump={props.onJump} /> :
          <div className="answer">
            <div className="answer-header"><AnchorTile /><span className="speaker">앵커</span>
              {turn.error && <span className="thread-status status-error"><span className="status-dot" aria-hidden="true" />오류</span>}
            </div>
            {turn.error ? <p className="answer-text" role="alert">{turn.error}</p> : turn.cancelled ?
              <p className="answer-text">답변 대기를 취소했습니다.</p> : <div className="pending-answer">
                <span role="status">{turn.mode === 'search' ? '검색 중…' : '검토 중…'}</span>
                <button className="text-button" type="button" onClick={props.onCancel}>대기 취소</button>
              </div>}
          </div>}
      </article>)}
      <div ref={end} />
    </div>
    {props.upload && <div className="upload-progress" role="status">
      <span>자료 추가 중{props.upload.total > 1 ? ` · ${props.upload.current}/${props.upload.total}` : ''}</span>
      <progress max={100} value={props.upload.progress} aria-label="자료 추가 진행률" />
      {props.upload.progress !== undefined && <span className="meta">{Math.round(props.upload.progress)}%</span>}
    </div>}
    <form className="composer-wrap" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      {constraint && <p className="composer-hint" id={hintId}>{constraint}</p>}
      <div className="composer">
        <textarea ref={input} aria-label="문서에 질문하기" placeholder="문서에 질문하기" rows={1} maxLength={4000}
          aria-describedby={constraint ? hintId : undefined} value={question} onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
              event.preventDefault(); submit();
            }
          }} />
        <IconButton label="전송" className="send-button" type="submit" disabled={!canSend}><ArrowUp size={20} /></IconButton>
      </div>
    </form>
  </aside>;
}
