import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server proxies /api + /auth → FastAPI :8001, /ws → WebSocket :9091.
// /auth/* MUST be same-origin with the React app so the session cookie set by
// /auth/callback is visible to subsequent fetches from React. Without this
// proxy the cookie would be issued for localhost:8001 and never sent back.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
      "/auth": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:9091",
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
