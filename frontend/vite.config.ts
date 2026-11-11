import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// Пути API проксируются на бэкенд в режиме разработки; браузер не делает внешних запросов.
const API_PATHS = ['/assessments', '/jobs', '/sources', '/evidence', '/health', '/trajectory', '/orbit', '/conjunctions', '/rules', '/manual'];
const API_TARGET = process.env.EVARISK_API_URL || 'http://localhost:8000';

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 4173,
    allowedHosts: ['terminal.local'],
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, { target: API_TARGET, changeOrigin: true }])),
  },
  build: { chunkSizeWarningLimit: 900 },
});
