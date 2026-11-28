import test from 'node:test';
import assert from 'node:assert/strict';
import { nextPdfResponse } from '../public/pdfResponse.js';

const url = 'https://example.test/report.pdf';
const start = (requestId = 'current', source = url) => ({ type: 'start', requestId, url: source });
const headers = (requestId = 'current', extra = {}) => ({ type: 'headers', requestId, url, mime: 'application/pdf', statusCode: 200, ...extra });
const failure = (requestId = 'current', error = 'net::ERR_ABORTED') => ({ type: 'error', requestId, url, error });
const pdf = () => nextPdfResponse(nextPdfResponse(undefined, start()), headers());

test('same-URL reload invalidates old PDF evidence and tracks the new request', () => {
  assert.deepEqual(nextPdfResponse(pdf(), start('new')), { url, requestId: 'new', loading: true });
});

test('late headers and errors from a superseded request cannot overwrite a reload', () => {
  const pending = nextPdfResponse(pdf(), start('new'));
  assert.strictEqual(nextPdfResponse(pending, headers()), pending);
  assert.strictEqual(nextPdfResponse(pending, failure()), pending);
  assert.strictEqual(nextPdfResponse(pending, failure('current', 'net::ERR_FAILED')), pending);
});

test('current successful PDF response survives the native viewer abort', () => {
  const value = pdf();
  assert.strictEqual(nextPdfResponse(value, failure()), value);
});

test('successful PDF MIME may contain case differences and parameters', () => {
  const value = nextPdfResponse(nextPdfResponse(undefined, start()), headers('current', { mime: ' Application/PDF; charset=binary ', statusCode: 206 }));
  assert.strictEqual(nextPdfResponse(value, failure()), value);
});

test('an abort before PDF headers records a failure', () => {
  const value = nextPdfResponse(nextPdfResponse(undefined, start()), failure());
  assert.deepEqual(value, { url, requestId: 'current', statusCode: 0, mime: '' });
});

test('an abort does not preserve HTML or unsuccessful PDF responses', () => {
  for (const response of [{ mime: 'text/html', statusCode: 200 }, { mime: 'application/pdf', statusCode: 404 }, { mime: 'application/pdf', statusCode: 302 }]) {
    const value = nextPdfResponse(nextPdfResponse(undefined, start()), headers('current', response));
    assert.equal(nextPdfResponse(value, failure()).statusCode, 0);
  }
});

test('other network errors clear even a successful PDF response', () => {
  assert.deepEqual(nextPdfResponse(pdf(), failure('current', 'net::ERR_CONNECTION_RESET')), { url, requestId: 'current', statusCode: 0, mime: '' });
});

test('redirect events cannot replace evidence for a different URL on the same request', () => {
  const finalUrl = 'https://example.test/download?id=42';
  const pending = nextPdfResponse(pdf(), start('current', finalUrl));
  assert.strictEqual(nextPdfResponse(pending, headers()), pending);
  assert.strictEqual(nextPdfResponse(pending, failure()), pending);
  assert.equal(nextPdfResponse(pending, headers('current', { url: finalUrl })).mime, 'application/pdf');
});

test('untracked or removed requests cannot recreate a response record', () => {
  assert.equal(nextPdfResponse(undefined, headers()), undefined);
  assert.equal(nextPdfResponse(undefined, failure()), undefined);
});

test('URL fragments are ignored when matching request evidence', () => {
  const pending = nextPdfResponse(undefined, start('current', `${url}#page=2`));
  const value = nextPdfResponse(pending, headers('current', { url: `${url}#page=7` }));
  assert.equal(value.url, url);
  assert.equal(value.statusCode, 200);
});
