import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    // Tiptap's Editor needs a DOM even headless; happy-dom is the light one.
    environment: "happy-dom",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
