import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// Settings come from the repo-root .env (see .env.example); only VITE_-prefixed variables are read here or exposed to the browser.
//   VITE_DEV_PORT        dev-server port (default 5173)
//   VITE_API_PROXY_TARGET where /api is forwarded (default http://localhost:8000 = the FastAPI layer in api/main.py)
//   VITE_API_BASE_URL    (src/api.ts) absolute API URL; leave empty to use the proxy
const envDir = decodeURIComponent(new URL('../..', import.meta.url).pathname).replace(/\/$/, ''); // repo root, no Node typings needed

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, envDir, 'VITE_');
  return {
    envDir,
    plugins: [react(), tailwindcss()],
    server: { port: Number(env.VITE_DEV_PORT || 5173), proxy: { '/api': { target: env.VITE_API_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true } } },
  };
});
