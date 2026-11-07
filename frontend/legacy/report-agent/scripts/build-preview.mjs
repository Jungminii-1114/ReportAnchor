import { build } from 'esbuild';
import { writeFile } from 'node:fs/promises';

// Bundle React into a classic inline script so this preview also works via file://.
const result = await build({
  entryPoints: ['src/main.jsx'], bundle: true, write: false,
  outdir: 'preview-assets', format: 'iife', jsx: 'automatic', minify: true,
  define: { 'process.env.NODE_ENV': '"production"', __BROWSER_PREVIEW__: 'true' }
});
const js = result.outputFiles.find(file => file.path.endsWith('.js')).text;
const css = result.outputFiles.find(file => file.path.endsWith('.css')).text;
const html = `<!doctype html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Report Agent 미리보기</title><style>${css.replaceAll('</style', '<\\/style')}</style></head><body><div id="root"></div><script>${js.replaceAll('</script', '<\\/script')}</script></body></html>`;
await writeFile('preview.html', html);
console.log('preview.html 생성 완료 — 더블클릭하여 열 수 있습니다.');
