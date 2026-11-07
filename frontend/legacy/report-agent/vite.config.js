import { defineConfig } from 'vite';
export default defineConfig({ base: './', define: { __BROWSER_PREVIEW__: 'false' }, esbuild: { jsx: 'automatic' } });
