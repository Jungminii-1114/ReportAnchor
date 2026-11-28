import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyTab } from '../public/pdfDetection.js';

const baseTab = { id: 42, windowId: 7, url: 'https://example.test/report.pdf', title: 'Report', status: 'complete' };
const tab = patch => ({ ...baseTab, ...patch });
const response = patch => ({ url: baseTab.url, mime: 'application/pdf', statusCode: 200, ...patch });

function found(result, verifiedBy) {
  assert.equal(result.status, 'found');
  assert.ok(result.document);
  assert.equal(result.document.verifiedBy, verifiedBy);
  return result.document;
}

function rejected(result) {
  assert.equal(result.status, 'error');
  assert.ok(typeof result.reason === 'string' && result.reason.length > 0);
  assert.equal(result.document, undefined);
}

test('a completed PDF path is identified from the URL when response evidence is unavailable', () => {
  const doc = found(classifyTab(baseTab, undefined, false), 'url');
  assert.equal(doc.name, 'report.pdf');
});

test('PDF extension matching is case insensitive', () => {
  found(classifyTab(tab({ url: 'https://example.test/Report.PDF' }), undefined, false), 'url');
});

test('a PDF MIME response recognizes an extensionless document URL', () => {
  const url = 'https://example.test/download?id=42';
  const doc = found(classifyTab(tab({ url, title: '사업 보고서' }), response({ url }), false), 'content-type');
  assert.equal(doc.name, '사업 보고서');
});

test('a successful partial-content PDF response is accepted', () => {
  found(classifyTab(baseTab, response({ statusCode: 206 }), false), 'content-type');
});

test('an HTML response overrides a misleading .pdf filename', () => {
  rejected(classifyTab(baseTab, response({ mime: 'text/html' }), false));
});

test('a 404 response is rejected even when its URL and MIME suggest PDF', () => {
  rejected(classifyTab(baseTab, response({ statusCode: 404 }), false));
});

test('an authentication failure is not reported as a found PDF', () => {
  rejected(classifyTab(baseTab, response({ statusCode: 403, mime: 'text/html' }), false));
});

test('a redirect response is not enough to verify a document', () => {
  rejected(classifyTab(baseTab, response({ statusCode: 302 }), false));
});

test('an extensionless page without MIME evidence asks the user to refresh', () => {
  const result = classifyTab(tab({ url: 'https://example.test/view?id=42' }), undefined, false);
  rejected(result);
  assert.match(result.reason, /새로\s*고침|refresh/i);
});

test('a query parameter containing .pdf does not turn an HTML page into a PDF', () => {
  rejected(classifyTab(tab({ url: 'https://example.test/view?file=report.pdf' }), undefined, false));
});

test('a hash containing .pdf does not turn an HTML page into a PDF', () => {
  rejected(classifyTab(tab({ url: 'https://example.test/view#report.pdf' }), undefined, false));
});

test('fragment changes retain matching MIME evidence and a canonical source URL', () => {
  const doc = found(classifyTab(tab({ url: 'https://example.test/report.pdf#page=5' }), response(), false), 'content-type');
  assert.equal(doc.url, 'https://example.test/report.pdf');
});

test('evidence for a different document does not verify an extensionless page', () => {
  rejected(classifyTab(tab({ url: 'https://example.test/other' }), response(), false));
});

test('stale non-PDF evidence for another URL does not reject the current PDF candidate', () => {
  found(classifyTab(baseTab, response({ url: 'https://example.test/previous', mime: 'text/html' }), false), 'url');
});

test('different query parameters identify different documents', () => {
  const currentUrl = 'https://example.test/download?id=2';
  rejected(classifyTab(tab({ url: currentUrl }), response({ url: 'https://example.test/download?id=1' }), false));
});

test('a loading tab remains searching instead of reusing a previous PDF decision', () => {
  const result = classifyTab(tab({ status: 'loading' }), { ...response(), loading: true }, false);
  assert.equal(result.status, 'searching');
  assert.equal(result.document, undefined);
});

test('a Korean PDF filename is decoded for display', () => {
  const name = '사업 보고서.pdf';
  const url = `https://example.test/reports/${encodeURIComponent(name)}`;
  const doc = found(classifyTab(tab({ url }), undefined, false), 'url');
  assert.equal(doc.name, name);
});

test('a local PDF requires the user-controlled file access setting', () => {
  rejected(classifyTab(tab({ url: 'file:///C:/Reports/report.pdf' }), undefined, false));
});

test('a permitted local PDF preserves the file URL', () => {
  const url = 'file:///C:/Reports/report.pdf';
  const doc = found(classifyTab(tab({ url }), undefined, true), 'url');
  assert.equal(doc.url, url);
});

test('local file permission does not make non-PDF files valid', () => {
  rejected(classifyTab(tab({ url: 'file:///C:/Reports/report.txt' }), undefined, true));
});

test('unsupported URL schemes do not report a document as found', async t => {
  for (const url of ['blob:https://example.test/document.pdf', 'chrome://settings/report.pdf', 'data:application/pdf;base64,JVBERg==', 'chrome-extension://other-id/report.pdf']) {
    await t.test(url.split(':')[0], () => rejected(classifyTab(tab({ url }), undefined, true)));
  }
});

test('source tab and window metadata are retained for expanded-view navigation', () => {
  const doc = found(classifyTab(tab({ id: 93, windowId: 18 }), response(), false), 'content-type');
  assert.equal(doc.tabId, 93);
  assert.equal(doc.windowId, 18);
});

test('a fresh PDF response is sufficient while Chrome PDF viewer is still loading', () => {
  found(classifyTab(tab({ status: 'loading' }), response(), false), 'content-type');
});
