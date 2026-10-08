/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Offline app: everything is bundled; no CDN at runtime (CLAUDE.md, Conventions).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    // Dev only: the app accepts its own Host/Origin (127.0.0.1:8765) and refuses others (SPEC §7, DNS
    // rebinding), so the proxy presents the request as same-origin.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        headers: { Origin: "http://127.0.0.1:8765" },
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}", "tools/**/*.test.ts"] },
});
