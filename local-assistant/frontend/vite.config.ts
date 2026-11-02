import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  plugins: [vue()],
  build: { outDir: '../web/ui', emptyOutDir: true },
  server: {
    host: '127.0.0.1',
    port: 5175,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8877',
      '/legacy.html': 'http://127.0.0.1:8877',
      '/app.js': 'http://127.0.0.1:8877',
      '/style.css': 'http://127.0.0.1:8877',
      '/integration.css': 'http://127.0.0.1:8877',
      '/integration-bridge.js': 'http://127.0.0.1:8877',
      '/evaluation.js': 'http://127.0.0.1:8877',
      '/evaluation.css': 'http://127.0.0.1:8877',
      '/qq-connector.js': 'http://127.0.0.1:8877',
    },
  },
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
})
