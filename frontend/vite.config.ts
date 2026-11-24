import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, Vite forwards /api to FastAPI, so the browser sees one origin
// (no CORS, and the login cookie works). In production nginx does the same job.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
