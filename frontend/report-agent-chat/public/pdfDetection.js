// Metadata detection only: no PDF body, parsing, or AI request.
export function documentUrl(raw) {
  try { const url = new URL(raw); url.hash = ''; return url.href; } catch { return ''; }
}
export function classifyTab(tab, evidence, fileAllowed = false) {
  const url = documentUrl(tab.url);
  let parsed;
  try { parsed = new URL(url); } catch { return { status: 'error', reason: '현재 탭의 주소를 확인할 수 없습니다.' }; }
  if (!['https:', 'http:', 'file:'].includes(parsed.protocol)) return { status: 'error', reason: '이 화면에서는 PDF를 직접 확인할 수 없습니다. PDF 파일의 원래 주소를 새 탭에서 열어 주세요.' };
  if (parsed.protocol === 'file:' && !fileAllowed) return { status: 'error', reason: '확장 프로그램의 세부정보에서 “파일 URL에 대한 액세스 허용”을 켜고 다시 찾아 주세요.' };
  let pathname = parsed.pathname;
  try { pathname = decodeURIComponent(pathname); } catch {}
  const isPdfPath = /\.pdf$/i.test(pathname);
  const known = evidence && documentUrl(evidence.url) === url;
  // Chrome's native PDF viewer can retain loading after the response arrives.
  if ((known && evidence.loading) || (!known && tab.status === 'loading')) return { status: 'searching' };
  let verifiedBy = 'url';
  if (known) {
    if (evidence.statusCode < 200 || evidence.statusCode >= 300) return { status: 'error', reason: '문서 요청이 완료되지 않았습니다. PDF가 정상적으로 열리는지 확인해 주세요.' };
    if (evidence.mime?.split(';')[0].trim().toLowerCase() === 'application/pdf') verifiedBy = 'content-type';
    else return { status: 'error', reason: '현재 탭은 PDF 응답이 아닙니다. 로그인 화면이나 일반 웹페이지인지 확인해 주세요.' };
  } else if (!isPdfPath) {
    return { status: 'error', reason: 'PDF를 확인하지 못했습니다. PDF가 열려 있다면 탭을 새로고침한 뒤 다시 찾아 주세요. 사이트 자체 뷰어는 지원하지 않습니다.' };
  }
  return { status: 'found', document: { tabId: tab.id, windowId: tab.windowId, url, name: isPdfPath ? pathname.split('/').pop() : (tab.title || 'PDF 문서'), verifiedBy } };
}
