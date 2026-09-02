import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Regex (leading ^), not the bare "/api" prefix this used to be.
      // A plain "/api" key matches any path *starting* with those characters,
      // so the SPA route /api-keys was proxied to the backend and answered
      // with {"detail":"Not Found"} — the page rendered blank on a direct
      // visit or reload, while clicking the sidebar link worked because that
      // is client-side routing with no HTTP request. Every API call in this
      // app goes through /api/v1 (axios baseURL in lib/api.js, plus the SSE
      // and window.open URLs), so anchoring to "^/api/" proxies exactly those
      // and leaves page routes alone.
      "^/api/": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
