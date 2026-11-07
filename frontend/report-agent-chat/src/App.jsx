import { useEffect, useRef, useState } from 'react';
import { ArrowUp, ArrowUpRight, BookOpen, Check, FileText, Loader2, Maximize2, MessageSquarePlus, PanelRightClose, PanelRightOpen, Plus, RotateCw, SlidersHorizontal, Square, X } from 'lucide-react';
import PreviewShell from './components/PreviewShell';
import Answer from './components/Answer';
import { mockResponse, suggestions } from './data/mockResponses';
import { useChat, setField, patchChat, getChat } from './services/chatState';
import { openExpanded, returnToPanel, closePanelForPdf } from './services/expandedView';

const expanded = new URLSearchParams(location.search).get('view') === 'expanded';


export default function App() {
  const { messages, draft, criteria, status, pending, ready, error } = useChat();
  const setDraft = value => setField('draft', value);
  const setCriteria = value => setField('criteria', value);
  const [showCriteria, setShowCriteria] = useState(false);
  const busy = Boolean(pending);
  const [phase, setPhase] = useState(0);
  const [panelOpen, setPanelOpen] = useState(true);
  const [notice, setNotice] = useState('');
  const [sourcePage, setSourcePage] = useState(1);
  const [showFound, setShowFound] = useState(false);
  const bottom = useRef(null);
  const input = useRef(null);

  useEffect(() => {
    if (!ready) return;
    const key = 'report-agent-chat.document-notice';
    if (status !== 'found') {
      setShowFound(false);
      try { sessionStorage.removeItem(key); } catch {}
      return;
    }
    try { if (sessionStorage.getItem(key)) return; } catch {}
    setShowFound(true);
    const timer = setTimeout(() => {
      setShowFound(false);
      try { sessionStorage.setItem(key, 'shown'); } catch {}
    }, 2500);
    return () => clearTimeout(timer);
  }, [status, ready]);
  useEffect(() => {
    const returnHandler = event => { if (event.data?.type === 'report-agent-return') setPanelOpen(true); };
    window.addEventListener('message', returnHandler);
    return () => window.removeEventListener('message', returnHandler);
  }, []);
  useEffect(() => { if (error) setNotice(error); }, [error]);
  useEffect(() => {
    if (status !== 'searching') return;
    const timer = setTimeout(() => setField('status', 'found'), 1400);
    return () => clearTimeout(timer);
  }, [status]);
  useEffect(() => {
    if (!pending) return;
    const age = Date.now() - pending.started;
    setPhase(age >= 1000 ? 1 : 0);
    const progress = setTimeout(() => setPhase(1), Math.max(0, 1000 - age));
    const finish = setTimeout(() => {
      const current = getChat();
      if (current.pending?.id !== pending.id) return;
      const answer = { id: pending.id, role: 'assistant', answer: mockResponse(pending.question) };
      patchChat({ messages: current.messages.some(m => m.id === pending.id) ? current.messages : [...current.messages, answer], pending: null });
    }, Math.max(0, 2300 - age));
    return () => { clearTimeout(progress); clearTimeout(finish); };
  }, [pending]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, busy, phase]);
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(''), 3500); return () => clearTimeout(t); }, [notice]);

  function findDocument() { setField('status', 'searching'); }
  function ask(text = draft) {
    const question = text.trim();
    if (!question || getChat().pending || status !== 'found' || !ready) return;
    const fullQuestion = criteria.trim() ? question + '\n평가 기준: ' + criteria.trim() : question;
    patchChat({
      messages: [...getChat().messages, { id: crypto.randomUUID(), role: 'user', text: fullQuestion }],
      draft: '', pending: { id: crypto.randomUUID(), question: fullQuestion, started: Date.now() },
    });
    setShowCriteria(false);
  }
  function stop() {
    patchChat({ pending: null, messages: [...getChat().messages, { id: crypto.randomUUID(), role: 'system', text: '답변 생성을 중단했습니다. 질문을 다시 보내 이어갈 수 있습니다.' }] });
  }
  function newChat() {
    patchChat({ pending: null, messages: [], draft: '', criteria: '', ...(status === 'searching' ? { status: 'error' } : {}) });
    setShowCriteria(false); input.current?.focus();
  }
  async function enlarge() {
    try { await openExpanded(); } catch (e) { setNotice(e.message || '전체화면을 열지 못했습니다.'); }
  }
  async function goBack() {
    try { await returnToPanel(); } catch (error) { setNotice(error.message || '사이드 패널로 돌아가지 못했습니다. 원래 문서 탭에서 확장 아이콘을 눌러 주세요.'); }
  }
  async function showPdf() {
    if (__BROWSER_PREVIEW__) { setPanelOpen(false); return; }
    try { await closePanelForPdf(); }
    catch { setNotice('패널을 닫지 못했습니다. Chrome 사이드 패널 상단의 닫기 버튼을 눌러 주세요.'); }
  }
  function viewSource(page) {
    if (__BROWSER_PREVIEW__ && !expanded) { setSourcePage(page); setPanelOpen(false); }
    else setNotice(`원문 ${page}페이지 이동은 실제 PDF 연결 후 사용할 수 있습니다.`);
  }

  const content = <div className={`chat-app ${expanded ? 'expanded' : ''}`}>
    <header className="chat-header"><div className="wordmark"><span className="brand-icon"><BookOpen size={20}/></span><strong>Report Agent</strong></div><div className="header-tools"><button className="view-button" title="새 대화" aria-label="새 대화" onClick={newChat} disabled={!ready}><MessageSquarePlus size={18}/><span>새 대화</span></button>{!expanded && <button className="view-button" title="패널을 닫고 PDF 크게 보기 · 다시 열려면 Chrome의 Report Agent 아이콘을 누르세요" aria-label="PDF 크게 보기" onClick={showPdf} disabled={!ready}><PanelRightClose size={17}/><span>PDF 크게 보기</span></button>}{!expanded && <button title="전체화면" aria-label="전체화면" className="view-button" onClick={enlarge} disabled={!ready}><Maximize2 size={17}/><span>전체화면</span></button>}{expanded && <button className="view-button return-button" title="사이드 패널로 돌아가기" aria-label="사이드 패널로 돌아가기" onClick={goBack}><PanelRightOpen size={17}/><span>사이드 패널로 돌아가기</span></button>}</div></header>
    {ready && (status !== 'found' || showFound) && <div className="document-area">
      {status === 'found' ? <div className="document-status document-found" role="status"><Check size={18}/><strong>문서를 찾았습니다</strong></div>
      : <div className="document-status" role="status">{status === 'searching' ? <Loader2 className="spin" size={19}/> : <FileText size={19}/>}<div><strong>{status === 'searching' ? '열려 있는 PDF를 찾고 있어요' : '연결된 문서가 없어요'}</strong><small>{status === 'searching' ? '잠시만 기다려 주세요.' : 'PDF를 연 뒤 문서를 연결해 주세요.'}</small></div>{status === 'error' && <button onClick={findDocument}><RotateCw size={13}/>다시 찾기</button>}</div>}
    </div>}
    <main className="conversation" aria-label="문서와 대화">
      {!messages.length ? <div className="welcome"><span className="welcome-eyebrow">문서를 읽는 새로운 방법</span><h1>궁금한 점을<br/>물어보세요</h1><p>보고서에서 궁금한 내용을 질문해 보세요.<br/>관련 내용이 있는 페이지도 함께 알려드려요.</p><div className="suggestions">{suggestions.map((s, i) => <button key={s.label} onClick={() => ask(s.text)} disabled={status !== 'found' || !ready}><span className="suggestion-icon">{i === 0 ? <FileText size={18}/> : i === 1 ? <BookOpen size={18}/> : <SlidersHorizontal size={18}/>}</span><span><strong>{s.label}</strong><small>{s.text}</small></span><ArrowUpRight size={15}/></button>)}</div><span className="welcome-note">질문은 자연스럽게, 기준은 구체적으로.</span></div>
      : <div className="messages">{messages.map(m => m.role === 'user' ? <div className="user-message" key={m.id}>{m.text}</div> : m.role === 'system' ? <p className="system-message" key={m.id}>{m.text}</p> : <article className="assistant-message" key={m.id}><div className="answer-label"><span className="mini-brand"><BookOpen size={13}/></span>Report Agent<span>예시 답변</span></div><Answer answer={m.answer} onSource={viewSource} onAsk={ask} busy={busy}/></article>)}{busy && <div className="thinking" role="status"><Loader2 size={15} className="spin"/>{phase === 0 ? '문서에서 관련 내용을 찾고 있어요' : '근거를 확인하고 답변을 정리하고 있어요'}<span className="thinking-dots">···</span></div>}</div>}
      <div ref={bottom}/>
    </main>
    <footer className="composer-area">
      {showCriteria && <div className="criteria-editor"><label htmlFor="criteria">평가 기준 <span>선택 사항</span></label><button aria-label="평가 기준 닫기" onClick={() => setShowCriteria(false)}><X size={15}/></button><textarea id="criteria" value={criteria} maxLength={1000} onChange={e => setCriteria(e.target.value)} placeholder="예: 예산 증가율 10% 이상, 핵심 과제 3개 이상"/><small>입력한 기준을 질문과 함께 검토합니다.</small></div>}
      <form className="composer" onSubmit={e => { e.preventDefault(); ask(); }}><label className="sr-only" htmlFor="question">문서에 질문하기</label><textarea ref={input} id="question" value={draft} maxLength={3000} disabled={status !== 'found' || !ready} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); ask(); } }} placeholder={status === 'found' ? '이 문서에 대해 무엇이 궁금하세요?' : '문서를 연결하면 대화를 시작할 수 있어요'}/><div className="composer-tools"><button type="button" className={`criteria-toggle ${criteria ? 'active' : ''}`} onClick={() => setShowCriteria(!showCriteria)} aria-expanded={showCriteria}><Plus size={15}/>{criteria ? '평가 기준 적용됨' : '평가 기준'}</button>{busy ? <button type="button" className="send" aria-label="답변 중단" onClick={stop}><Square size={14} fill="currentColor"/></button> : <button type="submit" className="send" aria-label="질문 보내기" disabled={!draft.trim() || status !== 'found' || !ready}><ArrowUp size={19}/></button>}</div></form>
      <p className="disclaimer">UI 미리보기 · 실제 PDF 분석 없이 예시 답변을 표시합니다.</p>
    </footer>
    {notice && <div className="toast" role="status">{notice}</div>}
  </div>;
  if (__BROWSER_PREVIEW__ && !expanded) return <PreviewShell panelOpen={panelOpen} setPanelOpen={setPanelOpen} sourcePage={sourcePage}>{content}</PreviewShell>;
  return <div className={expanded ? 'expanded-page' : 'extension-page'}>{content}</div>;
}
