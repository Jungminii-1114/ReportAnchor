const $ = id => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';
let library, info, activeDocument, response, selectedSource, currentTab = 'pdf';
let selectedIds = new Set(), mode = 'search', busy = false, uploadBusy = false, editingDocument;
let imageVersion = 0, documentVersion = 0, imageUrl;

function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function icon(name) {
  const svg = document.createElementNS(SVG, 'svg');
  const use = document.createElementNS(SVG, 'use');
  use.setAttribute('href', '#i-' + name); svg.append(use); svg.setAttribute('aria-hidden', 'true');
  return svg;
}
async function jsonFetch(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || '요청을 처리하지 못했습니다.');
  return data;
}
function post(path, body) {
  return jsonFetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
}
function notice(message, error = false) {
  $('notice').textContent = message; $('notice').classList.toggle('error', error); $('notice').hidden = !message;
}
function syncControls() {
  const blocked = busy || uploadBusy;
  $('search-button').disabled = blocked || !selectedIds.size || (mode === 'qa' && !library?.generation.enabled);
  $('export').disabled = blocked || !response;
  $('upload-button').disabled = blocked || !library?.upload.enabled;
  $('reset').disabled = blocked;
  $('top-k').disabled = blocked;
  $('model-apply').disabled = blocked;
  $('select-all').disabled = blocked;
  document.querySelectorAll('.document-row button, .document-row input, .sample').forEach(n => n.disabled = blocked);
  $('scope-caption').textContent = selectedIds.size ? `${selectedIds.size}개 자료를 검색 범위로 선택했어요.` : '검색할 자료의 체크박스를 선택하세요.';
  $('chat-scope').textContent = selectedIds.size ? `선택한 자료 ${selectedIds.size}개에서 근거를 확인합니다.` : '자료를 추가하거나 검색 범위를 선택하세요.';
  $('composer-hint').textContent = mode === 'qa' && !library?.generation.enabled
    ? '왼쪽 모델 설정에서 Ollama 모델을 적용하세요.' : '질문은 선택한 자료에서 새로 검토합니다.';
  $('mode-qa').disabled = blocked; $('mode-search').disabled = blocked;
}
function setMode(value) {
  mode = value;
  ['qa','search'].forEach(name => {
    $('mode-' + name).classList.toggle('active', name === mode);
    $('mode-' + name).setAttribute('aria-pressed', String(name === mode));
  });
  if (mode === 'qa' && !library?.generation.enabled) $('model-settings').open = true;
  syncControls();
}
function clearConversation() {
  response = undefined; $('chat-log').replaceChildren(); $('chat-intro').hidden = false;
  $('raw-json').textContent = '질문을 실행하면 표시됩니다.'; notice(''); syncControls();
}
function renderDocuments() {
  const filter = $('document-filter').value.trim().toLocaleLowerCase();
  const active = library.documents.filter(d => d.active);
  $('document-count').textContent = active.length;
  $('document-list').replaceChildren(); $('archived-list').replaceChildren();
  active.filter(d => d.title.toLocaleLowerCase().includes(filter)).forEach(doc => {
    const row = element('div', undefined, 'document-row');
    row.classList.toggle('active', doc.id === activeDocument);
    const check = element('input'); check.type = 'checkbox'; check.checked = selectedIds.has(doc.id);
    check.setAttribute('aria-label', `${doc.title} 검색 범위에 포함`);
    check.addEventListener('change', () => {
      check.checked ? selectedIds.add(doc.id) : selectedIds.delete(doc.id);
      clearConversation(); syncControls();
    });
    const open = element('button', undefined, 'document-open'); open.type = 'button'; open.title = doc.title;
    const copy = element('span', undefined, 'document-copy');
    copy.append(element('strong', doc.title), element('small', `${doc.page_count}쪽 · ${doc.chunk_count}개 청크`));
    open.append(icon('file'), copy);
    open.addEventListener('click', () => { clearConversation(); showDocument(doc.id); closeLibrary(); });
    const manage = element('button', '⋯', 'document-manage'); manage.title = '자료 관리'; manage.setAttribute('aria-label', doc.title + ' 관리');
    manage.addEventListener('click', () => {
      editingDocument = doc.id; $('document-name').value = doc.title;
      $('document-dialog').querySelector('.manage-description').textContent = '이름을 바꿔도 문서 ID와 원문은 유지됩니다.';
      $('document-dialog').showModal();
    });
    row.append(check, open, manage); $('document-list').append(row);
  });
  const archived = library.documents.filter(d => !d.active);
  $('archive-count').textContent = archived.length;
  archived.forEach(doc => {
    const row = element('div', undefined, 'document-row');
    const copy = element('span', undefined, 'document-copy'); copy.append(element('strong', doc.title), element('small', `${doc.page_count}쪽`));
    const restore = element('button', '복원', 'restore-button');
    restore.setAttribute('aria-label', doc.title + ' 복원');
    restore.addEventListener('click', async () => {
      try { await post('/api/documents/update', {document_id:doc.id, active:true}); await refreshLibrary(doc.id); }
      catch (error) { notice(error.message, true); }
    });
    row.append(copy, restore); $('archived-list').append(row);
  });
  $('library-empty').hidden = active.length > 0;
  if (active.length && !$('document-list').children.length) $('document-list').append(element('p', '이름과 일치하는 자료가 없어요.', 'scope-caption'));
  $('library-footer-title').textContent = active.length ? '자료함 준비 완료' : 'PDF를 추가해보세요';
  $('library-footer-detail').textContent = `${active.length}개 자료 · ${active.reduce((n,d) => n + d.page_count,0)}쪽 · 로컬 저장`;
  syncControls();
}
function renderModel() {
  const generation = library.generation;
  $('model-summary').textContent = generation.enabled ? generation.model : '검색 전용';
  if (document.activeElement !== $('model-input')) $('model-input').value = generation.model || '';
  $('provider').value = generation.provider || 'local';
  $('runtime-label').textContent = generation.test_response ? 'TEST · MOCK MODEL'
    : generation.enabled && generation.provider === 'cloud' ? 'OLLAMA CLOUD' : 'LOCAL WORKSPACE';
  $('runtime-badge').classList.toggle('cloud', generation.provider === 'cloud' || generation.test_response);
  $('model-state').textContent = generation.enabled ? generation.data_handling : '모델 없이 자료 업로드·검색·원문 확인을 사용할 수 있어요.';
  if (!library.upload.enabled) $('upload-state').textContent = 'PDF 업로드에는 PyMuPDF 환경이 필요합니다.';
  $('upload-state').hidden = library.upload.enabled && !uploadBusy;
}
async function refreshLibrary(selectNew) {
  library = await jsonFetch('/api/documents');
  const active = library.documents.filter(d => d.active);
  selectedIds = new Set([...selectedIds].filter(id => active.some(d => d.id === id)));
  if (selectNew && active.some(d => d.id === selectNew)) selectedIds = new Set([selectNew]);
  if (!selectedIds.size && active.length && !activeDocument) selectedIds.add(active[0].id);
  const next = selectNew || (active.some(d => d.id === activeDocument) ? activeDocument : active[0]?.id);
  renderDocuments(); renderModel();
  if (next) await showDocument(next);
  else {
    activeDocument = undefined; info = undefined; selectedSource = undefined; imageVersion++; documentVersion++;
    $('active-document-title').textContent = '자료를 선택하세요'; $('viewer-empty').hidden = false;
    $('pdf-image').hidden = true; $('pdf-status').hidden = true; $('source-context-header').hidden = true;
    $('page-caption').textContent = '자료 미선택'; $('page-select').disabled = true;
    $('previous-page').disabled = true; $('next-page').disabled = true; $('zoom').disabled = true;
  }
}
async function showDocument(id, source = null) {
  const version = ++documentVersion;
  imageVersion++; $('pdf-image').hidden = true; $('zoom').disabled = true;
  try {
    const data = await jsonFetch('/api/info?' + new URLSearchParams({document_id:id}));
    if (version !== documentVersion) return;
    activeDocument = id; info = data; selectedSource = source;
    $('active-document-title').textContent = data.title;
    $('viewer-empty').hidden = true; $('source-context-header').hidden = !source;
    $('source-title').textContent = source?.title || data.title;
    $('source-page').textContent = source ? '근거 · p.' + source.pages.join(', ') : '';
    $('page-select').max = data.page_count; $('page-select').value = source?.pages[0] || 1;
    $('page-select').disabled = !data.pdf_available;
    $('source-text').textContent = source?.context_text || '오른쪽에서 질문을 실행하고 근거를 선택하세요.';
    $('source-blocks').replaceChildren();
    (source?.context_source_spans || []).forEach((span, i) => {
      const detail = element('details');
      detail.append(element('summary', `${i + 1}. ${span.block_id} · 문자 ${span.start_char}–${span.end_char}`), element('pre', span.text));
      $('source-blocks').append(detail);
    });
    renderMetadata(source); renderDocuments(); loadPreview();
  } catch (error) { notice(error.message, true); }
}
function renderMetadata(source) {
  $('metadata').replaceChildren();
  const fields = [['자료', info.title], ['문서 ID', info.document_id], ['문서 버전 · SHA-256', info.version],
    ['페이지 / 청크 / 블록', `${info.page_count} / ${info.chunk_count} / ${info.block_count}`],
    ['추출 상태', 'PyMuPDF native text · 표 구조·OCR 정확도 미검증']];
  if (source) fields.push(['검색 청크', source.chunk_id], ['검색 점수', `${source.score.toFixed(4)} · 관련성 점수이며 확률이 아님`],
    ['원문 연결', `참조 블록 ${source.source_spans.length}개 · 복원 일치`], ['추출 검증 상태', source.verification_status]);
  fields.forEach(([key,value]) => $('metadata').append(element('dt', key), element('dd', String(value))));
}
async function loadPreview() {
  const version = ++imageVersion;
  $('pdf-image').hidden = true; $('zoom').disabled = true; $('pdf-status').hidden = false;
  $('previous-page').disabled = !info?.pdf_available || Number($('page-select').value) <= 1;
  $('next-page').disabled = !info?.pdf_available || Number($('page-select').value) >= info?.page_count;
  $('page-caption').textContent = info ? `p.${$('page-select').value} / ${info.page_count}` : '자료 미선택';
  if (!info?.pdf_available) { $('pdf-status').textContent = 'PDF 미연결 · 추출 텍스트에서 원문을 확인하세요.'; return; }
  $('pdf-status').textContent = '원문을 불러오고 있어요…';
  const args = {document_id:activeDocument, page:$('page-select').value};
  const showHighlight = selectedSource?.pages.includes(Number(args.page));
  if (showHighlight) { args.chunk_id = selectedSource.chunk_id; args.highlight = $('highlight').checked ? '1' : '0'; }
  $('preview-caption').hidden = !showHighlight;
  try {
    const result = await fetch('/api/preview?' + new URLSearchParams(args));
    if (!result.ok) throw new Error('PDF를 표시하지 못했어요. 추출 텍스트를 확인하세요.');
    const blob = await result.blob();
    if (version !== imageVersion) return;
    const next = URL.createObjectURL(blob);
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = next; $('pdf-image').src = next; $('pdf-image').hidden = false;
    $('pdf-status').hidden = true; $('zoom').disabled = false;
  } catch (error) { if (version === imageVersion) $('pdf-status').textContent = error.message; }
}
function showTab(name) {
  currentTab = name;
  document.querySelectorAll('[data-tab]').forEach(button => {
    const active = button.dataset.tab === name; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active));
  });
  ['pdf','text','metadata'].forEach(tab => $(tab + '-pane').hidden = tab !== name);
}
function selectSource(source, data) {
  response = data; $('raw-json').textContent = JSON.stringify(data, null, 2); syncControls();
  showDocument(source.document_key, source); showTab('pdf');
  if (innerWidth <= 580) document.querySelector('.evidence-panel').scrollIntoView({behavior:'smooth', block:'start'});
}
function addCitation(container, ref, data) {
  const source = data.sources.find(item => item.chunk_id === ref);
  if (!source) return;
  const button = element('button', undefined, 'citation-button'); button.type = 'button';
  button.append(icon('file'), element('span', `${source.title} · p.${source.pages.join(', ')}`));
  button.title = '원문 근거 보기'; button.addEventListener('click', () => selectSource(source, data)); container.append(button);
}
function answerHeader(article, label, cls, elapsed) {
  const heading = element('div', undefined, 'assistant-heading');
  const avatar = element('span', undefined, 'assistant-avatar'); avatar.append(icon('anchor'));
  heading.append(avatar, element('strong', '앵커'), element('span', label, 'answer-badge ' + cls),
    element('span', `${(elapsed / 1000).toFixed(1)}s`, 'answer-time'));
  article.append(heading);
}
function appendAnswer(data) {
  const article = element('article', undefined, 'assistant-message');
  if (data.kind === 'workflow') {
    const result = data.workflow_result;
    const label = result.status === 'error' ? '실행 오류' : result.outcome === 'partial_answer' ? '부분 답변'
      : result.outcome === 'insufficient_evidence' ? '확인 보류' : '근거 지지';
    answerHeader(article, label, result.status === 'error' ? 'error' : result.outcome ? 'partial' : '', data.elapsed_ms);
    if (result.status === 'error') {
      const box = element('div', undefined, 'unconfirmed-box contradicted');
      const formatError = ['INVALID_DRAFT_RESPONSE','INVALID_VERIFICATION_RESPONSE'].includes(result.error.error_code);
      box.append(element('strong', formatError ? '모델 응답 구조를 확인하지 못했어요.' : '검토를 완료하지 못했어요.'), element('p', formatError ? '모델은 응답했지만 답변 형식이나 근거 참조 검사를 통과하지 못했습니다. 서버를 최신 코드로 재시작한 뒤 다시 시도하세요.' : '선택한 자료와 Ollama 서버·모델 설정을 확인한 뒤 다시 시도하세요.'),
        element('p', `${result.error.node} · ${result.error.error_code}`)); article.append(box);
    }
    result.verified_claims.forEach(claim => {
      const row = element('div', undefined, 'verified-claim'); row.append(element('p', claim.claim_text));
      const citations = element('div', undefined, 'citation-row'); claim.evidence_refs.forEach(ref => addCitation(citations, ref, data)); row.append(citations); article.append(row);
    });
    if (!result.verified_claims.length && result.status !== 'error') article.append(element('p', '확보한 자료에서는 질문에 답할 내용을 충분히 확인하지 못했어요.', 'verified-claim'));
    result.unconfirmed_items.forEach(item => {
      const red = ['contradicted','revision_failed','revision_unavailable'].includes(item.reason_code);
      const box = element('div', undefined, 'unconfirmed-box' + (red ? ' contradicted' : ''));
      box.append(element('strong', red ? '원문과 충돌 · 확인되지 않은 항목' : '추가 확인이 필요한 항목'));
      if (item.claim_text) box.append(element('p', item.claim_text));
      box.append(element('p', item.reason));
      const citations = element('div', undefined, 'citation-row'); item.evidence_refs.forEach(ref => addCitation(citations, ref, data)); box.append(citations); article.append(box);
    });
    if (result.evidence_incomplete) article.append(element('p', '일부 자료 수집이 누락되었어요. 확보한 근거 범위에서만 검토한 결과입니다.', 'evidence-warning'));
    article.append(element('p', data.generation.test_response ? '화면 검사에 사용한 모의 모델 응답입니다. 실제 사실 검증 결과가 아닙니다.'
      : '추출 원문을 기준으로 한 모델 판정입니다. 중요한 판단은 원문 근거를 함께 확인하세요.', 'answer-footnote'));
  } else {
    const failed = data.retrieval.status === 'error' || data.source_context?.status === 'error';
    answerHeader(article, failed ? '실행 오류' : '검색 결과', failed ? 'error' : '', data.elapsed_ms);
    article.append(element('p', failed ? '검색이나 원문 조회를 완료하지 못했어요. 아래 실행 상세를 확인하세요.'
      : data.sources.length ? `관련 원문 ${data.sources.length}개를 찾았어요. 근거를 눌러 원문을 확인하세요.`
      : '이번 검색에서는 관련 근거를 찾지 못했어요. 문서 전체에 정보가 없다는 뜻은 아닙니다.', 'verified-claim'));
    data.sources.forEach(source => {
      const button = element('button', undefined, 'search-source');
      button.append(element('strong', `${source.title} · p.${source.pages.join(', ')}`), element('p', source.text.slice(0,200)));
      button.addEventListener('click', () => selectSource(source, data)); article.append(button);
    });
  }
  const details = element('details', undefined, 'run-details'); details.append(element('summary', '실행 과정과 JSON 보기'));
  const labels = {n01:'관련 근거 검색',n02:'원문 문맥 연결',n04:'답변 초안',n03:'주장·근거 대조',n04_revision:'모순 주장 1회 보정',n03_recheck:'수정 주장 재검증'};
  Object.entries(data.trace || {n01:data.retrieval,n02:data.source_context}).forEach(([key,result]) => {
    if (!result) return; const row = element('div', undefined, 'run-step'); row.classList.toggle('failed', result.status === 'error');
    row.append(element('span', labels[key] || key), element('span', result.status)); details.append(row);
  });
  details.append(element('pre', JSON.stringify(data.kind === 'workflow' ? data.workflow_result : data.retrieval,null,2))); article.append(details);
  $('chat-log').append(article); $('chat-scroll').scrollTop = $('chat-scroll').scrollHeight;
}
async function pollJob(id, update) {
  while (true) {
    const job = await jsonFetch('/api/jobs?' + new URLSearchParams({id}));
    if (update) update(job);
    if (job.status === 'completed') return job.result;
    if (job.status === 'error') throw new Error(job.error || '작업을 완료하지 못했어요.');
    await new Promise(resolve => setTimeout(resolve, 700));
  }
}
async function ask(query) {
  if (busy || uploadBusy || !query.trim()) return;
  if (!selectedIds.size) { notice('왼쪽에서 검색할 자료를 선택하세요.'); return; }
  if (mode === 'qa' && !library.generation.enabled) { $('model-settings').open = true; $('model-input').focus(); return; }
  busy = true; syncControls(); notice(''); $('chat-intro').hidden = true;
  $('chat-log').append(element('div', query, 'user-message'));
  const progress = element('div', undefined, 'status-card'); progress.append(element('span',undefined,'spinner'),element('span','선택한 자료에서 근거를 찾고 있어요…'));
  $('chat-log').append(progress); $('query').value = ''; $('chat-scroll').scrollTop = $('chat-scroll').scrollHeight;
  const ids = [...selectedIds]; const topK = Number($('top-k').value);
  try {
    let data;
    if (mode === 'qa') {
      const job = await post('/api/qa', {query,top_k:topK,document_ids:ids,request_ref:'web_' + crypto.randomUUID().replaceAll('-','')});
      data = await pollJob(job.id, state => { progress.lastElementChild.textContent = state.stage + '…'; });
    } else data = await post('/api/search', {query,top_k:topK,document_ids:ids});
    response = data; $('raw-json').textContent = JSON.stringify(data,null,2); appendAnswer(data);
    if (data.sources.length) selectSource(data.sources[0], data);
  } catch (error) { notice(error.message, true); }
  finally { progress.remove(); busy = false; syncControls(); $('query').focus(); }
}
async function uploadFiles(files) {
  if (busy || uploadBusy || !files.length || !library?.upload.enabled) return;
  uploadBusy = true; syncControls(); $('upload-state').hidden = false; $('upload-state').classList.remove('error');
  try {
    for (const file of files) {
      if (!file.name.toLowerCase().endsWith('.pdf') || file.size > library.upload.max_bytes) throw new Error('30MB 이하의 PDF 파일을 선택하세요.');
      $('upload-state').textContent = file.name + ' · 전송 중…';
      const job = await jsonFetch('/api/documents/upload?' + new URLSearchParams({filename:file.name}),
        {method:'POST',headers:{'Content-Type':'application/pdf'},body:file});
      const record = await pollJob(job.id, state => { $('upload-state').textContent = file.name + ' · ' + state.stage; });
      clearConversation(); await refreshLibrary(record.id);
      $('upload-state').hidden = false; $('upload-state').textContent = file.name + ' · 자료 추가 완료';
    }
  } catch (error) { $('upload-state').hidden = false; $('upload-state').classList.add('error'); $('upload-state').textContent = error.message; }
  finally { uploadBusy = false; $('upload-input').value = ''; syncControls(); }
}
function closeLibrary() { document.body.classList.remove('library-open'); $('library-toggle').setAttribute('aria-expanded','false'); }

$('search-form').addEventListener('submit', event => { event.preventDefault(); ask($('query').value); });
$('query').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); ask($('query').value); } });
document.querySelectorAll('.sample').forEach(button => button.addEventListener('click', () => ask(button.dataset.query)));
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => showTab(button.dataset.tab)));
$('mode-qa').addEventListener('click', () => setMode('qa')); $('mode-search').addEventListener('click', () => setMode('search'));
$('document-filter').addEventListener('input', renderDocuments);
$('select-all').addEventListener('click', () => { const ids = library.documents.filter(d => d.active).map(d => d.id); selectedIds = selectedIds.size === ids.length ? new Set() : new Set(ids); clearConversation(); renderDocuments(); });
$('upload-button').addEventListener('click', () => $('upload-input').click());
$('upload-input').addEventListener('change', () => uploadFiles([...$('upload-input').files]));
['dragenter','dragover'].forEach(type => $('drop-zone').addEventListener(type, event => { event.preventDefault(); $('drop-zone').classList.add('dragover'); }));
['dragleave','drop'].forEach(type => $('drop-zone').addEventListener(type, event => { event.preventDefault(); $('drop-zone').classList.remove('dragover'); if (type === 'drop') uploadFiles([...event.dataTransfer.files]); }));
$('show-archived').addEventListener('click', () => { $('archived-list').hidden = !$('archived-list').hidden; $('show-archived').setAttribute('aria-expanded', String(!$('archived-list').hidden)); });
$('close-document-dialog').addEventListener('click', () => $('document-dialog').close());
$('document-edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  try { await post('/api/documents/update',{document_id:editingDocument,title:$('document-name').value}); $('document-dialog').close(); await refreshLibrary(); }
  catch (error) { $('document-dialog').querySelector('.manage-description').textContent = error.message; }
});
$('archive-document').addEventListener('click', async () => {
  try { await post('/api/documents/update',{document_id:editingDocument,active:false}); $('document-dialog').close(); clearConversation(); await refreshLibrary(); }
  catch (error) { $('document-dialog').querySelector('.manage-description').textContent = error.message; }
});
$('model-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy || uploadBusy) return;
  busy = true; syncControls(); $('model-state').textContent = '모델 설정을 적용하고 있어요…';
  try { library.generation = await post('/api/model',{model:$('model-input').value,provider:$('provider').value}); renderModel(); clearConversation(); setMode('qa'); $('model-state').textContent = '설정 저장 완료 · 실제 실행 시 모델 연결을 확인합니다.'; }
  catch (error) { $('model-state').textContent = error.message; }
  finally { busy = false; syncControls(); }
});
function navigatePage(value) { if (!info) return; $('page-select').value = Math.max(1,Math.min(info.page_count,value)); loadPreview(); }
$('page-select').addEventListener('change', () => navigatePage(Number($('page-select').value) || 1));
$('previous-page').addEventListener('click', () => navigatePage(Number($('page-select').value)-1));
$('next-page').addEventListener('click', () => navigatePage(Number($('page-select').value)+1));
$('highlight').addEventListener('change', loadPreview);
$('zoom').addEventListener('click', () => { $('large-image').src = $('pdf-image').src; $('pdf-dialog').showModal(); });
$('close-dialog').addEventListener('click', () => $('pdf-dialog').close());
$('reset').addEventListener('click', clearConversation);
$('export').addEventListener('click', () => {
  if (!response) return; const url = URL.createObjectURL(new Blob([JSON.stringify(response,null,2)],{type:'application/json'}));
  const anchor = element('a'); anchor.href = url; anchor.download = `reportanchor-${response.request_ref || 'result'}.json`; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
$('library-toggle').addEventListener('click', () => { document.body.classList.toggle('library-open'); $('library-toggle').setAttribute('aria-expanded', String(document.body.classList.contains('library-open'))); });
$('close-library').addEventListener('click', closeLibrary);
refreshLibrary().then(() => setMode(library.generation.enabled ? 'qa' : 'search')).catch(error => { notice('자료함 연결 실패: '+error.message,true); });
