import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const devPort = 4173;
const apiTarget = 'http://127.0.0.1:8765';
const devOrigins = new Set([`http://127.0.0.1:${devPort}`, `http://localhost:${devPort}`]);

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: devPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: false,
        configure(proxy) {
          proxy.on('proxyReq', (proxyRequest, request) => {
            // Map the dev app's own origin to the daemon's allowed origin. Foreign origins still reach its guard unchanged.
            if (request.headers.origin && devOrigins.has(request.headers.origin)) proxyRequest.setHeader('Origin', apiTarget);
          });
        },
      },
    },
  },
});
