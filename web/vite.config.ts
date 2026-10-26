import { defineConfig } from 'vite';

// 開発中は /api を CloudFront（本番と同じ入口）へ中継する
export default defineConfig({
  server: {
    host: true,
    proxy: { '/api': { target: process.env.API_ORIGIN ?? 'https://agent.example.com', changeOrigin: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
});
