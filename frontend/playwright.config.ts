import { defineConfig, devices } from "@playwright/test";

// The real backend in scripted mode (no model), serving the built app: `npm run build` first. The sandboxes go
// to a throwaway directory. Needs the generated data (`uv run a15 generate`).
const PORT = 8765;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: 0,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `uv run a15 web --scripted --port ${PORT} --root runs/e2e-web`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
