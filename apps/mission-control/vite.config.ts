import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.SPACE_CODE_MISSION_CONTROL_API_URL || process.env.NEMOCODE_MISSION_CONTROL_API_URL || "http://127.0.0.1:8787";
const lmStudioTarget = (() => {
  const raw = process.env.LMSTUDIO_BASE_URL || "http://127.0.0.1:1234/v1";
  return raw.replace(/\/v1\/?$/, "");
})();

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": apiTarget,
      "/lm-proxy": { target: lmStudioTarget, changeOrigin: true, rewrite: (p) => p.replace(/^\/lm-proxy/, "") },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
