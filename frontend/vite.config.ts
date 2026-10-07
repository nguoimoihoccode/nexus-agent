import react from "@vitejs/plugin-react";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const host = env.NEXUS_FRONTEND_HOST || "127.0.0.1";
  const isLoopback = ["127.0.0.1", "localhost", "::1"].includes(host);
  if (!isLoopback && env.NEXUS_TRUSTED_LAN !== "1") {
    throw new Error("Non-loopback Vite binding requires NEXUS_TRUSTED_LAN=1.");
  }
  if (!isLoopback && !env.VITE_API_PROXY_TARGET) {
    throw new Error("Trusted-LAN Vite binding requires an explicit VITE_API_PROXY_TARGET.");
  }
  return {
    plugins: [
      tanstackRouter({ target: "react", autoCodeSplitting: true }),
      react(),
    ],
    test: {
      environment: "node",
      include: ["src/**/*.test.{ts,tsx}"],
    },
    server: {
      host,
      strictPort: true,
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY_TARGET || "http://127.0.0.1:2024",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
        "/auth": {
          target: env.VITE_API_PROXY_TARGET || "http://127.0.0.1:2024",
          changeOrigin: true,
        },
      },
    },
    preview: {
      host,
      strictPort: true,
    },
  };
});
