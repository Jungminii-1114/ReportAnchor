const $ = (id) => document.getElementById(id);
let info, response, selected, currentTab = 'pdf', searches = [], searching = false;
let imageVersion = 0;

function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
async function jsonFetch(path, options) {
  const result = await fetch(path, options);
  const data = await result.json();
  if (!result.ok) throw new Error(data.error || '요청에 실패했습니다.');
  return data;
}
function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = !message;
}
function setBusy(value) {
  searching = value;
  $('search-button').disabled = value;
  $('search-button').textContent = value ? '검색 중…' : '검색 ↗';
  $('reset').disabled = value;
  document.querySelectorAll('.sample').forEach(b => b.disabled = value);
}
function showTab(tab) {
  currentTab = tab;
  document.querySelectorAll('[data-tab]').forEach(b => {
    const active = b.dataset.tab === tab;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', String(active));
  });
  ['pdf', 'text', 'metadata'].forEach(name => $(name + '-pane').hidden = name !== tab);
}
function setMetadata(source) {
  $('metadata').replaceChildren();
  const fields = [
    ['청크 ID', source.chunk_id], ['문서 ID', source.document_id],
    ['문서 버전 · SHA-256', source.document_version],
    ['검색 순위 / BM25 점수', `${source.rank}위 / ${source.score.toFixed(6)} (확률이 아닙니다)`],
    ['PDF 물리 페이지', source.pages.join(', ') + ' (인쇄 쪽번호와 다를 수 있음)'],
    ['원문 연결', `블록 구간 ${source.source_spans.length}개 · 복원 일치`],
    ['문맥 보강', `원래 청크 ${source.text.length}자 → 주변 문맥 포함 ${source.context_text.length}자`],
    ['근거 항목 ID', source.evidence_ids.join(', ')],
    ['연결된 검색 청크 수', source.context_chunk_ids.length + '개 (같은 페이지의 겹치는 문맥은 병합)'],
    ['문맥 정책', `앞 ${info.context_config.before_chars}자 / 뒤 ${info.context_config.after_chars}자 · 전체 ${info.context_config.max_total_chars}자 상한`],
    ['해당 페이지 전체 블록', source.page_block_count + '개'],
    ['추출 정확도 검증 상태', source.verification_status + ' · 인식 정확도 미검증'],
    ['보강 문맥의 추출 상태', source.context_verification_status + ' · N-02에서 사실 검증하지 않음'],
    ['검색 방식', info.tokenizer + ' / BM25'],
  ];
  for (const [label, value] of fields) {
    $('metadata').append(element('dt', label), element('dd', String(value)));
  }
  $('raw-json').textContent = JSON.stringify(response.retrieval, null, 2);
  $('context-json').textContent = JSON.stringify(response.source_context, null, 2);
}
function loadPreview() {
  const version = ++imageVersion;
  $('pdf-image').hidden = true;
  $('zoom').disabled = true;
  $('pdf-status').hidden = false;
  if (!info.pdf_available) {
    $('pdf-status').textContent = 'PDF 미연결 · 추출 텍스트 탭에서 원문을 확인하세요.';
    return;
  }
  $('pdf-status').textContent = 'PDF 원문을 불러오고 있습니다…';
  const url = '/api/preview?' + new URLSearchParams({
    chunk_id: selected.chunk_id, page: $('page-select').value,
    highlight: $('highlight').checked ? '1' : '0',
  });
  const img = new Image();
  img.onload = () => {
    if (version !== imageVersion) return;
    $('pdf-image').src = url;
    $('pdf-image').hidden = false;
    $('pdf-status').hidden = true;
    $('zoom').disabled = false;
  };
  img.onerror = () => {
    if (version !== imageVersion) return;
    $('pdf-status').textContent = 'PDF를 표시하지 못했습니다. 추출 텍스트 탭을 확인하세요.';
  };
  img.src = url;
}
function selectSource(source, card) {
  selected = source;
  document.querySelectorAll('.result-card').forEach(b => {
    const active = b === card;
    b.classList.toggle('selected', active);
    b.setAttribute('aria-pressed', String(active));
  });
  $('evidence-empty').hidden = true;
  $('evidence').hidden = false;
  $('source-title').textContent = source.title;
  $('source-page').textContent = 'PDF p. ' + source.pages.join(', ');
  $('page-select').replaceChildren(...source.pages.map(p => {
    const option = element('option', String(p)); option.value = p; return option;
  }));
  $('source-text').textContent = source.context_text;
  $('source-blocks').replaceChildren();
  source.context_source_spans.forEach((span, i) => {
    const details = element('details');
    details.append(element('summary', `${i + 1}. ${span.block_id.split(':').slice(-2).join(':')} · 문자 ${span.start_char}–${span.end_char}`), element('pre', span.text));
    $('source-blocks').append(details);
  });
  setMetadata(source);
  showTab(currentTab);
  loadPreview();
}
function renderResults(data) {
  $('results').replaceChildren();
  $('evidence').hidden = true;
  $('evidence-empty').hidden = false;
  selected = undefined;
  imageVersion++;
  $('query-bubble').textContent = data.query;
  $('query-bubble').hidden = false;
  $('result-summary').replaceChildren(element('strong', `관련 청크 ${data.sources.length}개`),
    element('span', `${data.elapsed_ms.toLocaleString()} ms · BM25 검색`));
  $('result-summary').hidden = false;
  $('query-tokens').textContent = '질문 토큰  /  ' + (data.query_tokens.join(' · ') || '없음');
  $('query-tokens').hidden = false;
  if (data.retrieval.status === 'error') {
    const diagnostics = Object.values(data.retrieval.diagnostics);
    notice(diagnostics.map(d => `${d.error_code}: ${d.reason}`).join('\n'), true);
    return;
  }
  if (data.source_context?.status === 'error') {
    notice('원문 조회 오류: ' + Object.values(data.source_context.diagnostics)
      .filter(d => d.status === 'failed').map(d => d.error_code + ': ' + d.reason).join(' / '), true);
    return;
  }
  if (!data.sources.length) {
    notice('이번 검색에서는 관련 청크를 찾지 못했습니다. 검색어를 바꿔보세요. 문서에 해당 내용이 없다는 뜻은 아닙니다.');
    return;
  }
  notice(data.source_context?.status === 'partial' ? '일부 원문 조회가 누락되었습니다. 확보한 근거만 표시합니다.' : '');
  data.sources.forEach(source => {
    const card = element('button', undefined, 'result-card');
    card.type = 'button'; card.setAttribute('aria-pressed', 'false');
    const top = element('div', undefined, 'card-top');
    top.append(element('span', String(source.rank).padStart(2, '0'), 'rank'),
      element('strong', `PDF ${source.pages.join(', ')}페이지`), element('span', 'BM25 ' + source.score.toFixed(3), 'score'));
    const lines = source.text.split('\n').filter(s => s.trim());
    const title = lines.slice(0, 2).join(' ').slice(0, 90);
    const bottom = element('div', undefined, 'card-bottom');
    bottom.append(element('span', '복원 일치 ✓', 'tag'), element('span', `블록 구간 ${source.source_spans.length}개`, 'tag'), element('span', '원문 보기 →', 'link'));
    card.append(top, element('h3', title), element('p', source.text.slice(0, 330)), bottom);
    card.addEventListener('click', () => selectSource(source, card));
    $('results').append(card);
  });
  selectSource(data.sources[0], $('results').firstElementChild);
}
function renderHistory() {
  $('history').replaceChildren(...searches.slice(0, -1).slice(-4).map(query => {
    const b = element('button', query); b.type = 'button'; b.title = query;
    b.addEventListener('click', () => search(query)); return b;
  }));
}
async function search(query) {
  if (searching) return;
  setBusy(true); notice('');
  $('intro').hidden = true;
  $('export').disabled = true;
  // 이전 질문의 결과가 새 질문의 결과처럼 보이지 않도록 즉시 비운다.
  $('results').replaceChildren(); $('evidence').hidden = true; $('evidence-empty').hidden = false;
  $('result-summary').hidden = true; $('query-tokens').hidden = true;
  $('query-bubble').textContent = query; $('query-bubble').hidden = false;
  imageVersion++;
  try {
    const data = await jsonFetch('/api/search', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query, top_k: Number($('top-k').value)}),
    });
    response = data;
    renderResults(data);
    $('export').disabled = false;
    if (query.trim() && data.retrieval.status !== 'error') {
      searches.push(query); renderHistory();
    }
    $('query').value = '';
    $('search-scroll').scrollTop = 0;
  } catch (error) { response = undefined; notice(error.message, true); }
  finally { setBusy(false); $('query').focus(); }
}
$('search-form').addEventListener('submit', event => { event.preventDefault(); search($('query').value); });
$('query').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); search($('query').value);
  }
});
document.querySelectorAll('.sample').forEach(b => b.addEventListener('click', () => search(b.dataset.query)));
document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
$('page-select').addEventListener('change', loadPreview);
$('highlight').addEventListener('change', loadPreview);
$('zoom').addEventListener('click', () => { $('large-image').src = $('pdf-image').src; $('pdf-dialog').showModal(); });
$('close-dialog').addEventListener('click', () => $('pdf-dialog').close());
$('export').addEventListener('click', () => {
  if (!response) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(response, null, 2)], {type: 'application/json'}));
  const a = element('a'); a.href = url; a.download = `reportanchor-${response.retrieval.data?.request_ref || 'error'}.json`;
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('reset').addEventListener('click', () => {
  response = undefined; selected = undefined; searches = []; imageVersion++;
  $('intro').hidden = false; $('results').replaceChildren(); $('history').replaceChildren();
  ['query-bubble', 'result-summary', 'query-tokens', 'evidence'].forEach(id => $(id).hidden = true);
  $('evidence-empty').hidden = false; $('export').disabled = true; notice(''); $('query').value = ''; $('query').focus();
});
jsonFetch('/api/info').then(data => {
  info = data;
  $('doc-title').textContent = data.pdf_name || data.title;
  $('doc-subtitle').textContent = `${data.page_count}페이지 · 연결됨`;
  $('chunk-count').textContent = data.chunk_count.toLocaleString();
  $('block-count').textContent = data.block_count.toLocaleString();
}).catch(error => { notice('문서 연결 실패: ' + error.message, true); $('search-button').disabled = true; });
