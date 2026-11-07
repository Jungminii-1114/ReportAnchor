import { useSyncExternalStore } from 'react';
import { initialQuestion, initialConditions } from '../data/mockData';

// All views of this demo share one review. A backend can later scope this prefix
// by document/review ID. Each field is saved separately to avoid overwriting
// another view's unrelated edits.
const prefix = 'report-agent.review.v2.';
const defaults = { step: 1, question: initialQuestion, conditions: initialConditions, documentStatus: 'found', filter: 'all' };
const fields = Object.keys(defaults);
export const isExtension = Boolean(globalThis.chrome?.runtime?.id);
const listeners = new Set();
const pendingFields = new Map();
let snapshot = { value: { ...defaults }, ready: false, error: '' };
let pendingWrite = Promise.resolve();

function emit(patch) {
  snapshot = { ...snapshot, ...patch };
  for (const listener of listeners) listener();
}

function valid(field, value) {
  if (field === 'step') return Number.isInteger(value) && value >= 1 && value <= 4;
  if (field === 'question') return typeof value === 'string' && value.length <= 500;
  if (field === 'documentStatus') return ['searching', 'found', 'error'].includes(value);
  if (field === 'filter') return ['all', 'pass', 'fail', 'review'].includes(value);
  return field === 'conditions' && Array.isArray(value) && value.every(row => row &&
    ['string', 'number'].includes(typeof row.id) && ['field','operator','value','unit'].every(key => typeof row[key] === 'string'));
}

function accept(patch) {
  const clean = Object.fromEntries(Object.entries(patch).filter(([field,value]) => fields.includes(field) && valid(field,value)));
  if (Object.keys(clean).length) emit({ value: { ...snapshot.value, ...clean } });
}

function failed() {
  emit({ error: '화면 간 입력 내용 동기화에 실패했습니다. 크게 보기를 다시 시도해 주세요.' });
}

if (isExtension) {
  const changedDuringLoad = new Set();
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'session') return;
    const patch = {};
    for (const field of fields) {
      const change = changes[prefix + field];
      if (!change) continue;
      changedDuringLoad.add(field);
      if (!pendingFields.get(field)) patch[field] = change.newValue ?? defaults[field];
    }
    accept(patch);
  });
  chrome.storage.session.get(fields.map(field => prefix + field)).then(saved => {
    accept(Object.fromEntries(fields.filter(field => !changedDuringLoad.has(field)).map(field => [field, saved[prefix + field] ?? defaults[field]])));
    emit({ ready: true });
  }).catch(() => { failed(); emit({ ready: true }); });
} else {
  try {
    for (const field of fields) {
      const raw = localStorage.getItem(prefix + field);
      if (raw !== null) {
        try { accept({ [field]: JSON.parse(raw) }); } catch { /* Ignore an invalid old draft. */ }
      }
    }
    // Start a newly opened preview tab at the failed-search screen, even if a
    // previous visit left a completed review in localStorage. Reloading this
    // tab or opening its expanded view continues the current walkthrough.
    if (__BROWSER_PREVIEW__) {
      const walkthroughKey = 'report-agent.preview.walkthrough.v1';
      const expanded = new URLSearchParams(location.search).get('view') === 'expanded';
      if (!sessionStorage.getItem(walkthroughKey) && !expanded) {
        accept({ step: 1, documentStatus: 'error' });
        localStorage.setItem(prefix + 'step', '1');
        localStorage.setItem(prefix + 'documentStatus', JSON.stringify('error'));
      }
      sessionStorage.setItem(walkthroughKey, 'started');
    }
  } catch { failed(); }
  window.addEventListener('storage', event => {
    if (!event.key?.startsWith(prefix)) return;
    const field = event.key.slice(prefix.length);
    try { accept({ [field]: event.newValue === null ? defaults[field] : JSON.parse(event.newValue) }); } catch { /* Invalid external draft. */ }
  });
  emit({ ready: true });
}

function persist(patch) {
  if (!isExtension) {
    for (const [field,value] of Object.entries(patch)) localStorage.setItem(prefix + field, JSON.stringify(value));
    return;
  }
  const updatedFields = Object.keys(patch);
  for (const field of updatedFields) pendingFields.set(field, (pendingFields.get(field) || 0) + 1);
  const task = pendingWrite.catch(() => {}).then(() => chrome.storage.session.set(
    Object.fromEntries(Object.entries(patch).map(([field,value]) => [prefix + field, value]))
  ));
  pendingWrite = task;
  const finish = () => updatedFields.forEach(field => pendingFields.set(field, pendingFields.get(field) - 1));
  task.then(finish, () => { finish(); failed(); });
  return task;
}

export function setReviewField(field, update) {
  const value = typeof update === 'function' ? update(snapshot.value[field]) : update;
  if (!valid(field, value)) return;
  accept({ [field]: value });
  try { persist({ [field]: value }); } catch { failed(); }
}

// Called before opening another view. Writes even untouched defaults so the new
// view receives exactly the current review, and surfaces any storage failure.
export function flushReview() {
  try {
    const task = persist(snapshot.value);
    if (task) return task.then(() => emit({ error: '' }));
    emit({ error: '' });
  } catch (error) { failed(); throw error; }
}

export function useReviewState() {
  return useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener); }, () => snapshot);
}
