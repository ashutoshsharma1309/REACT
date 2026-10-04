/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The UI talks to the FastAPI backend through /api (proxied in dev and preview), so no CORS or
// backend URL is baked into the bundle. Override the target with REACT_API_TARGET.
const apiTarget = process.env.REACT_API_TARGET ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: apiTarget, changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api/, "") },
};

export default defineConfig({
  plugins: [react()],
  server: { port: Number(process.env.PORT ?? 5180), proxy },
  preview: { port: Number(process.env.PORT ?? 4173), proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
