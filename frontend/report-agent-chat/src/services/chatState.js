import { useSyncExternalStore } from 'react';
export const extension = Boolean(globalThis.chrome?.runtime?.id);
const prefix = 'report-agent-chat.v2.';
const defaults = { messages: [], draft: '', criteria: '', status: 'found', pending: null };
let snapshot = { ...defaults, ready: false };
const listeners = new Set();
let writes = Promise.resolve();
const dirty = new Set();
function accept(patch) { snapshot = { ...snapshot, ...patch }; listeners.forEach(fn => fn()); }
export function patchChat(patch) {
  accept(patch);
  const entries = Object.entries(patch);
  entries.forEach(([key]) => dirty.add(key));
  if (extension) {
    writes = writes.catch(() => {}).then(() => chrome.storage.session.set(Object.fromEntries(entries.map(([k,v]) => [prefix+k,v]))));
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
    const saved = extension ? await chrome.storage.session.get(keys.map(k => prefix+k)) : Object.fromEntries(keys.map(k => [prefix+k, JSON.parse(localStorage.getItem(prefix+k) || 'null')]));
    const patch = {};
    keys.forEach(k => { if (!dirty.has(k) && saved[prefix+k] !== undefined && saved[prefix+k] !== null) patch[k] = saved[prefix+k]; });
    accept(patch);
    if (__BROWSER_PREVIEW__) {
      const marker = 'report-agent-chat.walkthrough.v3';
      if (!sessionStorage.getItem(marker) && new URLSearchParams(location.search).get('view') !== 'expanded') patchChat({ status: 'searching' });
      sessionStorage.setItem(marker, 'started');
    }
  } catch { /* A new session is usable if storage is unavailable. */ }
  accept({ ready: true });
}
if (extension) chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'session') return;
  const patch = {};
  keys.forEach(k => { if (changes[prefix+k]) { dirty.add(k); patch[k] = changes[prefix+k].newValue ?? defaults[k]; } });
  if (Object.keys(patch).length) accept(patch);
});
else window.addEventListener('storage', e => {
  if (!e.key?.startsWith(prefix)) return;
  const key = e.key.slice(prefix.length);
  if (!keys.includes(key)) return;
  try { dirty.add(key); accept({ [key]: e.newValue === null ? defaults[key] : JSON.parse(e.newValue) }); } catch { /* Ignore malformed external data. */ }
});
init();
export function useChat() { return useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, () => snapshot); }
