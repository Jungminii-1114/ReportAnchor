export const mockFile = { name: 'AI_연구개발사업_2024.pdf', pages: 6 };
export const steps = [ ['문서 확인', '현재 탭의 PDF 감지'], ['평가 조건 입력', '질문과 기준 설정'], ['분석 진행', 'AI가 보고서 분석'], ['결과 확인', '평가 결과와 근거 확인'] ];
export const initialQuestion = '이 사업의 전체 예산은 2024년 기준으로 얼마이며, 전년 대비 증감률은 어떻게 되나요?';
export const examples = ['이 사업의 주요 추진 목적은 무엇인가요?', '각 세부 사업별 예산과 집행 기간을 정리해 주세요.', '이 사업의 성과 지표와 목표는 어떻게 설정되어 있나요?', '유사 사업과 비교했을 때 예산 규모는 적절한가요?'];
export const initialConditions = [{id:1, field:'전년 대비 증감률', operator:'≥', value:'10', unit:'%'}, {id:2, field:'총 사업 예산', operator:'≥', value:'500', unit:'억 원'}];
export const results = [
 {id:1, title:'전년 대비 예산 증가율 ≥ 10%', status:'pass', value:'12.4%', standard:'≥ 10%', quote:'2024년 총 사업 예산은 4,280억 원으로 전년 대비 12.4% 증가하였다.', page:2},
 {id:2, title:'핵심 추진 과제 3개 이상 포함', status:'pass', value:'4개', standard:'≥ 3개', quote:'본 사업은 4개의 핵심 추진 과제로 구성되어 있다.', page:3},
 {id:3, title:'성과 지표의 정량적 목표 설정', status:'fail', value:'2개', standard:'≥ 3개', quote:'정량적 성과 지표는 2개만 제시되어 추가 보완이 필요하다.', page:4},
 {id:4, title:'유사 사업과의 차별성 기술', status:'review', value:'일부 포함', standard:'명확한 차별성 제시', quote:'유사 사업 대비 차별성에 대한 설명이 일부만 포함되어 있다.', page:5},
 {id:5, title:'예산 집행 계획의 구체성', status:'pass', value:'상세히 기술됨', standard:'구체적 계획 제시', quote:'분기별 집행 계획과 세부 항목이 구체적으로 제시되어 있다.', page:6}
];
export const statusLabels = {pass:'충족', fail:'불충족', review:'검토 필요'};
