import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发模式：前端 5173，API 代理到 8000；生产构建产物供后端静态托管
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
