import { useEffect, useState } from 'react';
import { FileText, BookOpen, PanelRightOpen, ArrowLeft, ArrowRight, RotateCw, LockKeyhole } from 'lucide-react';
import { samplePdfBase64 } from '../data/samplePdf';
export default function PreviewShell({children,panelOpen,setPanelOpen,sourcePage=1}) {
 const [pdfUrl,setPdfUrl]=useState('');
 useEffect(()=>{const bytes=Uint8Array.from(atob(samplePdfBase64),char=>char.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));setPdfUrl(url);return()=>URL.revokeObjectURL(url);},[]);
 return <div className={`browser-preview ${panelOpen?'panel-open':'panel-closed'}`}>
  <div className="preview-banner"><span><span className="preview-dot"/>사이드 패널 사용 예시</span><small>실제 PDF 감지·분석 기능은 연결되지 않은 UI입니다.</small></div>
  <div className="browser-tab-row"><div className="browser-tab"><span className="tiny-pdf">PDF</span>AI_연구개발사업_2024.pdf<span>×</span></div><span className="tab-plus">+</span></div>
  <div className="browser-address-row"><div className="browser-navigation" aria-hidden="true"><ArrowLeft size={17}/><ArrowRight size={17}/><RotateCw size={16}/></div><div className="browser-address"><LockKeyhole size={14}/><span>예시 문서 / AI_연구개발사업_2024.pdf</span></div><button className="panel-launch extension-action" title="Report Agent 열기 (Chrome 확장 아이콘 예시)" aria-label="Report Agent 열기" onClick={()=>setPanelOpen(true)}><BookOpen size={18}/></button></div>
  <div className="preview-layout"><section className="pdf-pane" aria-label="예시 PDF 원문"><div className="pdf-context-bar"><span><FileText size={16}/>PDF 원문 <small>· 6페이지</small></span></div>{pdfUrl&&<iframe className="native-pdf" title="AI 연구개발사업 2024 예시 PDF" src={`${pdfUrl}#toolbar=1&view=FitH&page=${sourcePage}`}/>}</section><aside className="preview-panel" aria-label="Report Agent 사이드 패널" hidden={!panelOpen}><div className="side-panel-caption"><span><PanelRightOpen size={14}/>Report Agent 사이드 패널</span></div>{children}</aside></div>
 </div>;
}
