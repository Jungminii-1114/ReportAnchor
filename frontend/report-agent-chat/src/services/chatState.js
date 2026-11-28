import { useSyncExternalStore } from 'react';
export const extension = Boolean(globalThis.chrome?.runtime?.id);
const prefix = 'report-agent-chat.v2.';
const detectionKey = 'report-agent-chat.detection.v1';
const defaults = { messages: [], draft: '', criteria: '', status: extension ? 'searching' : 'found', pending: null };
function detectionPatch(value) { return { status: value?.status || 'searching', document: value?.document || null, detectionReason: value?.reason || '', source: value?.source || null }; }
let snapshot = { ...defaults, ready: false };
const listeners = new Set();
let writes = Promise.resolve();
const dirty = new Set();
export function sourceIdentity(source = snapshot.source) {
  let url = source?.url || '';
  try { const parsed = new URL(url); parsed.hash = ''; url = parsed.href; } catch {}
  return `${source?.tabId ?? ''}:${url}`;
}
function accept(patch) {
  snapshot = { ...snapshot, ...patch };
  if (extension) {
    const identity = sourceIdentity();
    snapshot.messages = snapshot.messages.filter(message => message.sourceIdentity === identity);
    if (snapshot.pending?.sourceIdentity !== identity) snapshot.pending = null;
  }
  listeners.forEach(fn => fn());
}
export function patchChat(patch) {
  const identity = sourceIdentity();
  const documentWrite = extension && ('messages' in patch || 'pending' in patch);
  if (documentWrite) {
    patch = { ...patch };
    if (patch.messages) patch.messages = patch.messages.map(message => ({ ...message, sourceIdentity: message.sourceIdentity ?? identity }));
    if (patch.pending) patch.pending = { ...patch.pending, sourceIdentity: patch.pending.sourceIdentity ?? identity };
  }
  accept(patch);
  const entries = Object.entries(patch);
  entries.forEach(([key]) => dirty.add(key));
  if (extension) {
    writes = writes.catch(() => {}).then(async () => {
      if (documentWrite) {
        if (identity !== sourceIdentity()) return;
        const stored = await chrome.storage.session.get(detectionKey);
        if (identity !== sourceIdentity() || identity !== sourceIdentity(stored[detectionKey]?.source || null)) return;
      }
      await chrome.storage.session.set(Object.fromEntries(entries.map(([k,v]) => [prefix+k,v])));
    });
    writes.catch(() => accept({ error: '대화를 저장하지 못했습니다.' }));
  } else {
    try { entries.forEach(([k,v]) => localStorage.setItem(prefix+k, JSON.stringify(v))); }
    catch { accept({ error: '대화를 저장하지 못했습니다.' }); }
  }
}
export function setField(key, value) { patchChat({ [key]: typeof value === 'function' ? value(snapshot[key]) : value }); }
export function getChat() { return snapshot; }
export function flushChat() { return writes; }
const keys = Object.keys(defaults);
async function init() {
  try {
    const saved = extension ? await chrome.storage.session.get([...keys.map(k => prefix+k), detectionKey]) : Object.fromEntries(keys.map(k => [prefix+k, JSON.parse(localStorage.getItem(prefix+k) || 'null')]));
    const patch = {};
    keys.forEach(k => { if (!dirty.has(k) && saved[prefix+k] !== undefined && saved[prefix+k] !== null) patch[k] = saved[prefix+k]; });
    if (extension && !dirty.has(detectionKey)) Object.assign(patch, detectionPatch(saved[detectionKey]));
    accept(patch);
    if (__BROWSER_PREVIEW__) {
      const marker = 'report-agent-chat.walkthrough.v3';
      if (!sessionStorage.getItem(marker) && new URLSearchParams(location.search).get('view') !== 'expanded') patchChat({ status: 'searching' });
      sessionStorage.setItem(marker, 'started');
    }
  } catch { /* A new session is usable if storage is unavailable. */ }
  accept({ ready: true });
  if (extension) void detectDocument();
}
if (extension) chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'session') return;
  const patch = {};
  keys.forEach(k => { if (changes[prefix+k]) { dirty.add(k); patch[k] = changes[prefix+k].newValue ?? defaults[k]; } });
  if (changes[detectionKey]) { dirty.add(detectionKey); Object.assign(patch, detectionPatch(changes[detectionKey].newValue)); }
  if (Object.keys(patch).length) accept(patch);
});
else window.addEventListener('storage', e => {
  if (!e.key?.startsWith(prefix)) return;
  const key = e.key.slice(prefix.length);
  if (!keys.includes(key)) return;
  try { dirty.add(key); accept({ [key]: e.newValue === null ? defaults[key] : JSON.parse(e.newValue) }); } catch { /* Ignore malformed external data. */ }
});
export async function detectDocument() {
  if (!extension) { setField('status', 'searching'); return; }
  accept({ status: 'searching', detectionReason: '' });
  try {
    const window = await chrome.windows.getCurrent();
    const params = new URLSearchParams(location.search);
    const raw = params.get('sourceTab');
    let sourceTab;
    if (params.get('view') === 'expanded') {
      const latest = (await chrome.storage.session.get(detectionKey))[detectionKey]?.source;
      sourceTab = Number.isInteger(latest?.tabId) ? latest.tabId : raw !== null && Number.isInteger(Number(raw)) ? Number(raw) : undefined;
    }
    const response = await chrome.runtime.sendMessage({ type: 'report-agent:detect', windowId: window.id, sourceTab });
    if (!response?.ok) throw new Error(response?.error || '문서를 확인하지 못했습니다.');
    const stored = await chrome.storage.session.get(detectionKey);
    accept(detectionPatch(stored[detectionKey]));
  } catch (error) { accept({ status: 'error', document: null, detectionReason: error.message }); }
}
init();
export function useChat() { return useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, () => snapshot); }
