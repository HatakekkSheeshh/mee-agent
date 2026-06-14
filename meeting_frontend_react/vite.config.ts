import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server runs on :8001 (the host registered as the Azure OAuth
// redirect_uri http://localhost:8001/auth/callback) and proxies /api + /auth →
// FastAPI :8002, /ws → WebSocket :9091.
// /auth/* MUST be same-origin with the React app so the session cookie set by
// /auth/callback is visible to subsequent fetches from React. Microsoft
// redirects the browser to :8001/auth/callback → this proxy forwards it to the
// backend; the backend pins redirect_uri via MS_REDIRECT_URI so it stays :8001.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8001,
    proxy: {
      "/api": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
      "/auth": {
        target: "http://localhost:8002",
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
