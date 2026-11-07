import { flushReview, isExtension } from './reviewState';

export const isExpandedView = new URLSearchParams(location.search).get('view') === 'expanded';

export function openExpandedView() {
  if (isExtension) return openExtensionTab();
  // Keep this synchronous with the click so browsers allow the new tab.
  flushReview();
  const url = new URL(location.href);
  url.searchParams.set('view', 'expanded');
  const opened = window.open(url.href, '_blank');
  if (!opened) throw new Error('새 탭이 차단되었습니다. 이 파일의 팝업을 허용한 후 다시 시도해 주세요.');
}

async function openExtensionTab() {
  await flushReview();
  const [source] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = new URL(chrome.runtime.getURL('index.html'));
  url.searchParams.set('view', 'expanded');
  if (source?.id !== undefined) url.searchParams.set('sourceTab', source.id);
  if (source?.windowId !== undefined) url.searchParams.set('sourceWindow', source.windowId);
  const tab = await chrome.tabs.create({ url: url.href, active: false });
  // Keep the original document's panel available, but avoid showing a second
  // copy of it alongside the expanded page.
  await chrome.sidePanel.setOptions({ tabId: tab.id, enabled: false });
  await chrome.tabs.update(tab.id, { active: true });
}

export function returnToPanel() {
  if (isExtension) {
    const params = new URLSearchParams(location.search);
    const sourceWindow = params.get('sourceWindow');
    const sourceTab = params.get('sourceTab');
    if (!sourceWindow || !Number.isInteger(Number(sourceWindow))) {
      throw new Error('Chrome 도구 모음의 Report Agent 아이콘을 눌러 사이드 패널을 열어 주세요.');
    }
    // Invoke directly in the click handler to retain Chrome's user gesture.
    const opening = chrome.sidePanel.open(sourceTab && Number.isInteger(Number(sourceTab))
      ? { tabId: Number(sourceTab) }
      : { windowId: Number(sourceWindow) });
    return opening.then(async () => {
      if (sourceTab && Number.isInteger(Number(sourceTab))) {
        try { await chrome.tabs.update(Number(sourceTab), { active: true }); }
        catch { throw new Error('원래 문서 탭이 닫혔습니다. PDF 탭을 열고 Report Agent 아이콘을 눌러 주세요.'); }
      }
      await chrome.windows.update(Number(sourceWindow), { focused: true });
    });
  }
  flushReview();
  if (window.opener && !window.opener.closed) {
    window.opener.focus();
    window.close();
  } else {
    const url = new URL(location.href);
    url.searchParams.delete('view');
    location.assign(url.href);
  }
}
