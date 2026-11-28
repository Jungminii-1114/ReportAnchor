import { classifyTab, documentUrl } from './pdfDetection.js';
import { nextPdfResponse } from './pdfResponse.js';
const detectionKey = 'report-agent-chat.detection.v1';
const prefix = 'report-agent-chat.v2.';
const responseKey = id => `report-agent-chat.pdf-response.${id}`;
let generation = 0;
let selectedTab = null;
let commits = Promise.resolve();
const responseWrites = new Map();
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);
const ownPage = url => url?.startsWith(chrome.runtime.getURL(''));

function queueResponse(tabId, update) {
  const write = (responseWrites.get(tabId) || Promise.resolve()).catch(() => {}).then(update);
  responseWrites.set(tabId, write);
  const cleanup = () => { if (responseWrites.get(tabId) === write) responseWrites.delete(tabId); };
  write.then(cleanup, cleanup);
  return write;
}
function recordResponse(details, type) {
  if (details.tabId < 0) return;
  const event = {
    type, requestId: details.requestId, url: details.url, error: details.error,
    mime: details.responseHeaders?.find(h => h.name.toLowerCase() === 'content-type')?.value || '',
    statusCode: details.statusCode,
  };
  queueResponse(details.tabId, async () => {
    const key = responseKey(details.tabId);
    const previous = (await chrome.storage.session.get(key))[key];
    const next = nextPdfResponse(previous, event);
    if (next === previous) return false;
    await chrome.storage.session.set({ [key]: next });
    return true;
  }).then(async changed => {
    if (!changed) return;
    const source = await currentSource();
    if (selectedTab === details.tabId || source?.tabId === details.tabId) await detect(details.tabId);
  }).catch(console.error);
}

function commit(value, ticket) {
  commits = commits.catch(() => {}).then(async () => {
    if (ticket !== generation) return;
    const previous = (await chrome.storage.session.get(detectionKey))[detectionKey];
    if (ticket !== generation) return;
    const identity = `${value.source?.tabId ?? ''}:${documentUrl(value.source?.url)}`;
    const oldIdentity = `${previous?.source?.tabId ?? ''}:${documentUrl(previous?.source?.url)}`;
    const changed = identity !== oldIdentity;
    // One shared review follows the selected source document in both views.
    const changes = { [detectionKey]: value };
    if (changed || !previous) Object.assign(changes, { [prefix+'messages']: [], [prefix+'pending']: null });
    await chrome.storage.session.set(changes);
  });
  return commits;
}
async function detect(tabId) {
  const ticket = ++generation;
  selectedTab = tabId;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (ownPage(tab.url)) return;
    await responseWrites.get(tabId);
    const stored = await chrome.storage.session.get(responseKey(tabId));
    const fileAllowed = tab.url?.startsWith('file:') ? await chrome.extension.isAllowedFileSchemeAccess() : false;
    const result = classifyTab(tab, stored[responseKey(tabId)], fileAllowed);
    return await commit({ ...result, source: { tabId, windowId: tab.windowId, url: documentUrl(tab.url) } }, ticket);
  } catch {
    return await commit({ status: 'error', document: null, reason: '문서 탭이 닫혔거나 접근할 수 없습니다. PDF 탭을 선택한 뒤 다시 찾아 주세요.', source: { tabId } }, ticket);
  }
}
async function currentSource() {
  return (await chrome.storage.session.get(detectionKey))[detectionKey]?.source;
}
async function onActivated(info) {
  const tab = await chrome.tabs.get(info.tabId);
  if (ownPage(tab.url)) return; // Expanded chat must keep its source document.
  const source = await currentSource();
  if (source?.windowId !== undefined && source.windowId !== info.windowId) return;
  await detect(info.tabId);
}
chrome.tabs.onActivated.addListener(info => { onActivated(info).catch(console.error); });
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (!change.url && !change.status) return;
  currentSource().then(source => { if (selectedTab === tabId || source?.tabId === tabId) return detect(tabId); }).catch(console.error);
});
chrome.tabs.onRemoved.addListener(tabId => {
  queueResponse(tabId, () => chrome.storage.session.remove(responseKey(tabId))).catch(console.error);
  currentSource().then(source => { if (selectedTab === tabId || source?.tabId === tabId) return detect(tabId); }).catch(console.error);
});
// Invalidate old MIME evidence before every top-level navigation, including reload.
chrome.webRequest.onBeforeRequest.addListener(details => {
  recordResponse(details, 'start');
}, { urls: ['http://*/*', 'https://*/*'], types: ['main_frame'] });
chrome.webRequest.onErrorOccurred.addListener(details => {
  recordResponse(details, 'error');
}, { urls: ['http://*/*', 'https://*/*'], types: ['main_frame'] });
// Observe only top-level response metadata, not the PDF's bytes or page contents.
chrome.webRequest.onHeadersReceived.addListener(details => {
  recordResponse(details, 'headers');
}, { urls: ['http://*/*', 'https://*/*'], types: ['main_frame'] }, ['responseHeaders']);
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || message?.type !== 'report-agent:detect') return;
  (async () => {
    let id = message.sourceTab;
    if (!Number.isInteger(id)) {
      const [tab] = await chrome.tabs.query({ active: true, windowId: message.windowId });
      id = tab?.id;
      if (ownPage(tab?.url)) id = (await currentSource())?.tabId;
    }
    if (!Number.isInteger(id)) throw new Error('PDF 탭을 먼저 열어 주세요.');
    await detect(id);
    sendResponse({ ok: true });
  })().catch(error => sendResponse({ ok: false, error: error.message }));
  return true;
});
