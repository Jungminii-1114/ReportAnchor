import { build } from 'vite';
import config from '../vite.config.js';
await build({ ...config, configFile: false });
await import('./build-preview.mjs');
