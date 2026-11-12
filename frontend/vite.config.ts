import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // Bind IPv4 explicitly: the default "localhost" resolves to ::1 on this
    // machine, which leaves http://127.0.0.1:5173 unreachable.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
});
