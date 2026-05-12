import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.SPACE_CODE_MISSION_CONTROL_API_URL || process.env.NEMOCODE_MISSION_CONTROL_API_URL || "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
