import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config: dev server proxies /api → FastAPI :8001 and /ws → WebSocket :9091
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
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
