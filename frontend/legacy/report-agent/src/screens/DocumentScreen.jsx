import { FileText, Check, ArrowRight, RefreshCw, PanelRightClose, ScanSearch, ExternalLink, FileSearch, LoaderCircle } from 'lucide-react';
import { PageTitle } from '../components/Layout';
export default function DocumentScreen({status='found',onNext,onViewPdf,onRetry}) {
 const found=status==='found';
 return <>
  <PageTitle title="1. 문서 확인">Chrome에서 보고 있는 PDF를 확인합니다.</PageTitle>
  {found?<>
  <div className="detection-state" role="status"><span className="detection-check"><Check size={17}/></span><strong>현재 탭의 PDF를 확인했습니다.</strong></div>
  <section className="detected-document"><div className="document-cover"><FileText size={44} strokeWidth={1.5}/><span>2024</span><strong>AI 연구개발사업</strong><small>연구개발 사업 보고서</small><div className="cover-lines"><i/><i/><i/></div></div><div className="detected-file-info"><span className="eyebrow-label">현재 탭의 문서</span><h2>AI_연구개발사업_2024.pdf</h2><div className="document-meta"><span>PDF 문서</span><span>6페이지</span><span>예시 보고서</span></div><div className="tab-source"><span className="source-dot"/>Chrome에서 열려 있는 PDF</div></div><button className="button document-view" onClick={onViewPdf}><ExternalLink size={16}/>PDF 원문 보기</button></section>
  <div className="document-action-note"><PanelRightClose size={19}/><p>패널을 접으면 PDF를 넓게 볼 수 있습니다.<br/>상단의 ‘크게 보기’를 클릭하면 새 탭에서 전체 화면으로 검토할 수 있습니다.</p></div>
  </>:status==='searching'?<section className="document-search-state" role="status" aria-live="polite">
   <div className="search-illustration" aria-hidden="true"><FileText size={35} strokeWidth={1.5}/><LoaderCircle className="search-spinner" size={23}/></div>
   <h2>PDF 문서를 찾고 있습니다.</h2><p>현재 탭에 열린 문서를 확인하고 있습니다.<br/>잠시만 기다려 주세요.</p>
   <div className="document-skeleton" aria-hidden="true"><span/><div><i/><i/><i/></div></div>
  </section>:<section className="document-search-state search-failed" role="status" aria-live="polite">
   <div className="search-illustration" aria-hidden="true"><FileSearch size={36} strokeWidth={1.5}/></div>
   <h2>PDF 문서를 찾지 못했습니다.</h2><p>현재 탭에서 PDF가 열려 있는지 확인한 후<br/>다시 시도해 주세요.</p>
   <button className="button" onClick={onRetry}><RefreshCw size={16}/>다시 찾기</button>
   <div className="search-help"><strong>문서가 열려 있는데도 찾지 못하나요?</strong><p>PDF가 완전히 로드되었는지 확인하거나,<br/>PDF를 새 탭에서 열어 보세요.</p></div>
  </section>}
  <p className="document-demo-note">예시 화면입니다. 실제 PDF 감지 기능은 연결 전입니다.</p>
  <footer className="footer end"><button className="button primary" disabled={!found} onClick={onNext}><ScanSearch size={18}/>{found?'이 문서 검토하기':status==='searching'?'문서 확인 중':'문서를 확인해 주세요'}{found&&<ArrowRight size={18}/>}</button></footer>
 </>;
}
