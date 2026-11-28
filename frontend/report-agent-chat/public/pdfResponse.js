import { documentUrl } from './pdfDetection.js';

// Ignore events from superseded requests, including redirects and same-URL reloads.
export function nextPdfResponse(previous, event) {
  const url = documentUrl(event.url);
  const requestId = event.requestId;
  if (event.type === 'start') return { url, requestId, loading: true };
  if (!previous || previous.requestId !== requestId || documentUrl(previous.url) !== url) return previous;
  if (event.type === 'headers') return { url, requestId, mime: event.mime, statusCode: event.statusCode };
  if (event.type === 'error') {
    const isPdf = previous.statusCode >= 200 && previous.statusCode < 300
      && previous.mime?.split(';')[0].trim().toLowerCase() === 'application/pdf';
    // Chrome may abort the navigation after handing a PDF to its native viewer.
    if (event.error === 'net::ERR_ABORTED' && isPdf) return previous;
    return { url, requestId, statusCode: 0, mime: '' };
  }
  return previous;
}
