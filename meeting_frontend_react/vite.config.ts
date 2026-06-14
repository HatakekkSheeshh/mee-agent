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
      // 127.0.0.1 (not "localhost"): on Windows "localhost" resolves to IPv6
      // ::1 first, but uvicorn binds IPv4 0.0.0.0 — the explicit IPv4 host
      // avoids an ECONNREFUSED on the IPv6 attempt.
      "/api": {
        target: "http://127.0.0.1:8002",
        changeOrigin: true,
      },
      "/auth": {
        target: "http://127.0.0.1:8002",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:9091",
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
