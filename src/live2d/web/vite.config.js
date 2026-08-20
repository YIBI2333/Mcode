import { defineConfig } from 'vite';
export default defineConfig({
  server: { port: 5188, strictPort: true,
    proxy: { '/live2d-resources': 'http://localhost:8866', '/model': 'http://localhost:8866' }
  },
  resolve: {
    alias: {
      // 强制所有 pixi-live2d-display import 使用 Cubism 4 only 版本
      // 无需 Cubism 2 运行时，无需任何 mock
      'pixi-live2d-display': 'pixi-live2d-display/dist/cubism4.es.js',
    }
  }
});
