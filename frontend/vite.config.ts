import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backendTarget = env.VITE_BACKEND_URL || process.env.VITE_BACKEND_URL || 'http://localhost:8000';
  const devApiKey = env.VITE_DEV_API_KEY || process.env.VITE_DEV_API_KEY;

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
      },
    },
    optimizeDeps: {
      include: ['msw/browser'],
    },
    server: {
      port: 5173,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (devApiKey) proxyReq.setHeader('X-API-Key', devApiKey);
            });
          },
          rewrite: (p) => p,
        },
        '/admin': {
          target: backendTarget,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (devApiKey) proxyReq.setHeader('X-API-Key', devApiKey);
            });
          },
          rewrite: (p) => p,
        },
        '/metrics': {
          target: backendTarget,
          changeOrigin: true,
          rewrite: (p) => p,
        },
        '/health': {
          target: backendTarget,
          changeOrigin: true,
          rewrite: (p) => p,
        },
        '/stream': {
          target: backendTarget,
          changeOrigin: true,
          ws: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (devApiKey) proxyReq.setHeader('X-API-Key', devApiKey);
            });
            // Inject API key for WebSocket upgrade requests as well
            proxy.on('proxyReqWs', (proxyReq) => {
              if (devApiKey) proxyReq.setHeader('X-API-Key', devApiKey);
            });
          },
          rewrite: (p) => p,
        },
        '/ws': {
          target: backendTarget,
          changeOrigin: true,
          ws: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (devApiKey) proxyReq.setHeader('X-API-Key', devApiKey);
            });
            // Inject API key for WebSocket upgrade requests
            proxy.on('proxyReqWs', (proxyReq) => {
              if (devApiKey) proxyReq.setHeader('X-API-Key', devApiKey);
            });
          },
          rewrite: (p) => p,
        },
      },
    },
  };
});
