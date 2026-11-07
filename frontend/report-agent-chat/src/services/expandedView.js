import { extension, flushChat } from './chatState';
export function openExpanded() {
  const url = new URL(extension ? chrome.runtime.getURL('index.html') : location.href);
  url.searchParams.set('view', 'expanded');
  if (!extension) {
    if (!window.open(url.href, '_blank')) throw new Error('새 탭을 열려면 팝업을 허용해 주세요.');
    return;
  }
  return (async () => {
    await flushChat();
    const [source] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (source?.id !== undefined) url.searchParams.set('sourceTab', source.id);
    if (source?.windowId !== undefined) url.searchParams.set('sourceWindow', source.windowId);
    const tab = await chrome.tabs.create({ url: url.href, active: false });
    await chrome.sidePanel.setOptions({ tabId: tab.id, enabled: false });
    await chrome.tabs.update(tab.id, { active: true });
  })();
}
export function returnToPanel() {
  if (extension) {
    const params = new URLSearchParams(location.search);
    const source = params.get('sourceTab');
    if (!source || !Number.isInteger(Number(source))) throw new Error('돌아갈 문서 탭 정보가 없습니다. PDF 탭에서 확장 아이콘으로 사이드 패널을 열어 주세요.');
    const tabId = Number(source);
    // Start during the user gesture; do not await storage or tab queries first.
    let opening;
    try { opening = chrome.sidePanel.open({ tabId }); }
    catch (error) { opening = Promise.reject(error); }
    return Promise.resolve(opening).then(async () => {
      try { await chrome.tabs.update(tabId, { active: true }); }
      catch { throw new Error('원래 문서 탭으로 이동하지 못했습니다. PDF 탭을 직접 선택해 주세요.'); }
      if (params.get('sourceWindow')) await chrome.windows.update(Number(params.get('sourceWindow')), { focused: true }).catch(() => {});
    }, async () => {
      try { await chrome.tabs.get(tabId); }
      catch { throw new Error('원래 PDF 탭이 닫혔습니다. PDF를 다시 열고 확장 아이콘을 눌러 주세요.'); }
      await chrome.tabs.update(tabId, { active: true }).catch(() => {});
      throw new Error('문서 탭은 열려 있지만 사이드 패널을 자동으로 열지 못했습니다. Chrome 도구 모음의 Report Agent Chat 아이콘을 눌러 주세요.');
    });
  }
  const url = new URL(location.href);
  url.searchParams.delete('view');
  const fallback = () => location.replace(url.href);
  try {
    if (window.opener && !window.opener.closed) {
      // file:// pages can have opaque origins. Never access the opener DOM.
      window.opener.postMessage({ type: 'report-agent-return' }, '*');
      window.opener.focus();
      // Embedded browsers may refuse window.close(); recover in this tab.
      setTimeout(fallback, 250);
      window.close();
      return;
    }
  } catch { /* Cross-window access may be restricted in an embedded browser. */ }
  fallback();
}

// Close the real global side panel, leaving the browser's PDF tab visible.
export async function closePanelForPdf() {
  if (!extension) throw new Error('PDF 크게 보기는 Chrome 확장 또는 미리보기에서 사용할 수 있습니다.');
  await flushChat();
  const window = await chrome.windows.getCurrent();
  await chrome.sidePanel.close({ windowId: window.id });
}
