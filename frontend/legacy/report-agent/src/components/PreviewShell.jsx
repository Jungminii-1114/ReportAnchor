import { useEffect, useState } from 'react';
import { FileText, PanelRightClose, PanelRightOpen, ArrowLeft, ArrowRight, RotateCw, LockKeyhole, Maximize2, Minimize2 } from 'lucide-react';
import { samplePdfBase64 } from '../data/samplePdf';
export default function PreviewShell({children,panelOpen,setPanelOpen}) {
 const [pdfUrl,setPdfUrl]=useState(''); const [focus,setFocus]=useState(false);
 useEffect(()=>{const bytes=Uint8Array.from(atob(samplePdfBase64),char=>char.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));setPdfUrl(url);return()=>URL.revokeObjectURL(url);},[]);
 return <div className={`browser-preview ${panelOpen?'panel-open':'panel-closed'} ${focus?'pdf-focus':''}`}>
  <div className="preview-banner"><span><span className="preview-dot"/>사이드 패널 사용 예시</span><small>실제 PDF 감지·분석 기능은 연결되지 않은 UI입니다.</small></div>
  <div className="browser-tab-row"><div className="browser-tab"><span className="tiny-pdf">PDF</span>AI_연구개발사업_2024.pdf<span>×</span></div><span className="tab-plus">+</span></div>
  <div className="browser-address-row"><div className="browser-navigation" aria-hidden="true"><ArrowLeft size={17}/><ArrowRight size={17}/><RotateCw size={16}/></div><div className="browser-address"><LockKeyhole size={14}/><span>예시 문서 / AI_연구개발사업_2024.pdf</span></div><button className={`panel-launch ${panelOpen?'is-open':''}`} onClick={()=>{setPanelOpen(!panelOpen);setFocus(false);}} aria-expanded={panelOpen}>{panelOpen?<PanelRightClose size={17}/>:<PanelRightOpen size={17}/>}<span>{panelOpen?'패널 접기':'Report Agent 열기'}</span></button></div>
  <div className="preview-layout"><section className="pdf-pane" aria-label="예시 PDF 원문"><div className="pdf-context-bar"><span><FileText size={16}/>PDF 원문 <small>· 6페이지</small></span><button onClick={()=>{if(focus){setFocus(false);setPanelOpen(true);}else{setFocus(true);setPanelOpen(false);}}}>{focus?<Minimize2 size={16}/>:<Maximize2 size={16}/>}<span>{focus?'검토 화면으로 돌아가기':'PDF 넓게 보기'}</span></button></div>{pdfUrl&&<iframe className="native-pdf" title="AI 연구개발사업 2024 예시 PDF" src={`${pdfUrl}#toolbar=1&view=FitH`}/>}</section><aside className="preview-panel" aria-label="Report Agent 사이드 패널" hidden={!panelOpen}><div className="side-panel-caption"><span><PanelRightOpen size={14}/>Report Agent 사이드 패널</span><button aria-label="사이드 패널 접기" onClick={()=>setPanelOpen(false)}><PanelRightClose size={17}/></button></div>{children}</aside></div>
  <div className="mobile-view-switch"><button className={!panelOpen?'selected':''} onClick={()=>setPanelOpen(false)}><FileText size={16}/>PDF 원문</button><button className={panelOpen?'selected':''} onClick={()=>{setFocus(false);setPanelOpen(true);}}><PanelRightOpen size={16}/>Report Agent</button></div>
 </div>;
}
