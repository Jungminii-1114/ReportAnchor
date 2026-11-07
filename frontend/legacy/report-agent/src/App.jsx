import { useEffect, useState } from 'react';
import { Header, Stepper, Toast } from './components/Layout';
import DocumentScreen from './screens/DocumentScreen';
import PreviewShell from './components/PreviewShell';
import ConditionsScreen from './screens/ConditionsScreen';
import ProgressScreen from './screens/ProgressScreen';
import ResultsScreen from './screens/ResultsScreen';
import { mockFile } from './data/mockData';
import { useReviewState, setReviewField } from './services/reviewState';
import { isExpandedView, openExpandedView, returnToPanel } from './services/expandedView';

export default function App() {
  const {value:review,ready,error}=useReviewState();
  const {step,question,conditions,documentStatus,filter}=review;
  const [notice,setNotice]=useState('');
  const [panelOpen,setPanelOpen]=useState(true);
  const [opening,setOpening]=useState(false);
  const isPreview=__BROWSER_PREVIEW__;

  // UI-only search simulation; any open view can finish it after a reload.
  useEffect(()=>{
    if(documentStatus!=='searching')return;
    const timer=setTimeout(()=>setReviewField('documentStatus','found'),1500);
    return()=>clearTimeout(timer);
  },[documentStatus]);
  useEffect(()=>{if(error)setNotice(error);},[error]);

  const changeView=action=>{
    setOpening(true);
    try {
      const result=action();
      Promise.resolve(result).catch(err=>setNotice(err.message||'화면을 열지 못했습니다. 다시 시도해 주세요.')).finally(()=>setOpening(false));
    } catch(err) {setNotice(err.message||'화면을 열지 못했습니다. 다시 시도해 주세요.');setOpening(false);}
  };
  const retryDocument=()=>{setReviewField('documentStatus','searching');setNotice('');};
  const navigate=next=>{
    if(next>1&&documentStatus!=='found'){setNotice('PDF 문서가 확인되면 다음 단계로 진행할 수 있습니다.');return;}
    setReviewField('step',next);setNotice('');window.scrollTo({top:0});
  };
  const viewPdf=()=>{
    if(isExpandedView){changeView(returnToPanel);return;}
    if(isPreview){setPanelOpen(false);return;}
    setNotice('현재 탭의 PDF를 넓게 보려면 Chrome 사이드 패널의 닫기 버튼을 누르세요.');
  };

  if(!ready)return <div className="review-loading" role="status">검토 내용을 불러오고 있습니다.</div>;
  const panel=<div className={`app ${isExpandedView?'expanded-app':''}`}>
    <Header onNotice={setNotice} onExpand={()=>changeView(openExpandedView)} onReturn={()=>changeView(returnToPanel)} expanded={isExpandedView} opening={opening}/>
    {isExpandedView&&<div className="expanded-context"><span>넓게 보기</span><p>질문과 평가 조건은 사이드 패널과 함께 반영됩니다.</p></div>}
    <div className="workspace">
      <Stepper step={step} onStep={navigate} file={documentStatus==='found'?mockFile:null}/>
      <main key={step}>
        {step===1?<DocumentScreen status={documentStatus} onNext={()=>navigate(2)} onViewPdf={viewPdf} onRetry={retryDocument}/>
        :step===2?<ConditionsScreen question={question} setQuestion={value=>setReviewField('question',value)} conditions={conditions} setConditions={value=>setReviewField('conditions',value)} onBack={()=>navigate(1)} onNext={()=>navigate(3)}/>
        :step===3?<ProgressScreen onStop={()=>{navigate(2);setNotice('예시 분석이 중단되었습니다. 평가 조건을 수정할 수 있습니다.');}} onNext={()=>navigate(4)}/>
        :<ResultsScreen onBack={()=>navigate(2)} onNotice={setNotice} filter={filter} setFilter={value=>setReviewField('filter',value)}/>}
      </main>
    </div>
    <Toast message={notice} onClose={()=>setNotice('')}/>
  </div>;
  if(isExpandedView)return <div className="expanded-page">{panel}</div>;
  return isPreview?<PreviewShell panelOpen={panelOpen} setPanelOpen={setPanelOpen}>{panel}</PreviewShell>:panel;
}
