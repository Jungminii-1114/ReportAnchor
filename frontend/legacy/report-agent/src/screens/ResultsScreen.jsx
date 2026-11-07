import { Download, ArrowLeft, ArrowUpRight } from 'lucide-react';
import { PageTitle } from '../components/Layout';
import { results, statusLabels, mockFile } from '../data/mockData';

const reviewNotes = {
  1: '예산 증가율은 12.4%로 기준인 10% 이상을 충족합니다.',
  2: '기술 개발, 데이터 구축, 현장 실증, 인재 양성의 4개 과제를 확인했습니다.',
  3: '정량적 목표는 2개로, 기준인 3개에 미치지 못합니다. 추가 지표가 필요합니다.',
  4: '유사 사업과의 연계 방향은 있으나, 대상과 지원 범위를 비교한 설명은 부족합니다.',
  5: '분기별 집행 비율과 추진 일정이 제시되어 있습니다.'
};
const titles = {1:'전년 대비 예산 증가율',2:'핵심 추진 과제',3:'정량적 성과 목표',4:'유사 사업과의 차별성',5:'예산 집행 계획'};
const criteria = {1:'10% 이상',2:'3개 이상',3:'3개 이상',4:'명확한 차별성 제시',5:'구체적 집행 계획'};

export default function ResultsScreen({onBack,onNotice,filter='all',setFilter}) {
  const visibleResults=results.filter(result=>filter==='all'||result.status===filter);
  return <div className="review-report">
    <PageTitle title="4. 검토 결과">{mockFile.name}</PageTitle>
    <div className="review-overview" aria-label="검토 결과 요약">
      {Object.entries(statusLabels).map(([key,label])=><button key={key} className={`review-total ${key}`} onClick={()=>setFilter(key)} aria-pressed={filter===key}>
        <span className="review-total-label">{label}</span><strong>{results.filter(result=>result.status===key).length}<small>건</small></strong>
      </button>)}
    </div>
    <p className="review-lead">5개 조건 중 3개를 충족했습니다. 성과 지표와 사업 차별성은 추가 확인이 필요합니다.</p>
    <div className="review-filter-bar" aria-label="검토 결과 필터">
      {[['all','전체'],...Object.entries(statusLabels)].map(([key,label])=><button key={key} className={filter===key?'active':''} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}<span>{key==='all'?results.length:results.filter(result=>result.status===key).length}</span></button>)}
    </div>
    <div className="review-items">
      {visibleResults.map(result=><article className="review-item" key={result.id}>
        <div className="review-item-heading"><span className="review-index">{String(result.id).padStart(2,'0')}</span><h2>{titles[result.id]}</h2><span className={`review-status ${result.status}`}>{statusLabels[result.status]}</span></div>
        <div className="review-measure"><span>확인 내용 <strong>{result.value}</strong></span><span>평가 기준 <strong>{criteria[result.id]}</strong></span></div>
        <p className="review-observation">{reviewNotes[result.id]}</p>
        <div className="review-source"><blockquote>{result.quote}</blockquote><button onClick={()=>onNotice(`원문 ${result.page}페이지 이동은 PDF 뷰어 연결 후 제공됩니다.`)} aria-label={`원문 ${result.page}페이지 보기`}>원문 {result.page}쪽<ArrowUpRight size={13}/></button></div>
      </article>)}
    </div>
    <p className="review-footnote">예시 보고서 기준의 검토 결과입니다.</p>
    <footer className="footer"><button className="button" onClick={onBack}><ArrowLeft size={17}/>이전 단계</button><button className="button primary" onClick={()=>onNotice('보고서 다운로드는 백엔드 연결 후 제공됩니다.')}><Download size={17}/>보고서 다운로드</button></footer>
  </div>;
}
