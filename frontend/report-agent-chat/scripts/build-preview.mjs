import { build } from 'vite';
import { writeFile } from 'node:fs/promises';
const result = await build({ configFile: false, base: './', define: { __BROWSER_PREVIEW__: 'true' }, esbuild: { jsx: 'automatic' }, build: { write: false, cssCodeSplit: false, rollupOptions: { input: 'src/main.jsx', output: { format: 'iife', inlineDynamicImports: true } } } });
const js = result.output.find(file => file.type === 'chunk').code;
const css = result.output.find(file => file.fileName.endsWith('.css')).source;
const html = `<!doctype html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Report Agent Chat 미리보기</title><style>${css.replaceAll('</style', '<\\/style')}</style></head><body><div id="root"></div><script>${js.replaceAll('</script', '<\\/script')}</script></body></html>`;
await writeFile('preview.html', html);
console.log('preview.html 생성 완료');
