import { ArrowUpRight, Check, ChevronDown, FileText } from 'lucide-react';
export default function Answer({ answer, onSource, onAsk, busy }) {
  return <div className="answer-body">
    <p>{answer.intro}</p>
    {answer.facts && <div className="facts">{answer.facts.map(f => <div key={f.label}><span>{f.label}</span><strong>{f.value}<small>{f.unit}</small></strong></div>)}</div>}
    {answer.bullets && <ul>{answer.bullets.map(t => <li key={t}>{t}</li>)}</ul>}
    {answer.rows && <div className="review-list">{answer.rows.map(row => <div className="review-row" key={row.title}><span className="checkmark"><Check size={14}/></span><div><strong>{row.title}</strong><small>보고서 수치 {row.value} · <span>충족</span></small></div><button onClick={() => onSource(row.page)} aria-label={`원문 ${row.page}페이지 보기`}>{row.page}페이지 <ArrowUpRight size={13}/></button></div>)}</div>}
    {answer.note && <p className="answer-note">{answer.note}</p>}
    {answer.quote && <details className="evidence"><summary><FileText size={14}/>근거 확인 <span>{answer.page}페이지</span><ChevronDown size={14}/></summary><blockquote>{answer.quote}</blockquote><button onClick={() => onSource(answer.page)}>원문 {answer.page}페이지 보기 <ArrowUpRight size={13}/></button></details>}
    <div className="followups">{answer.followups.map(q => <button key={q} disabled={busy} onClick={() => onAsk(q)}>{q}<ArrowUpRight size={13}/></button>)}</div>
  </div>;
}
