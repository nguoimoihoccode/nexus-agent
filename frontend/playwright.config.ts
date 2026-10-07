import { defineConfig, devices } from "@playwright/test";

const loopbackNoProxy = [...new Set([
  ...(process.env.NO_PROXY || "").split(","),
  ...(process.env.no_proxy || "").split(","),
  "127.0.0.1",
  "localhost",
].filter(Boolean))].join(",");
process.env.NO_PROXY = loopbackNoProxy;
process.env.no_proxy = loopbackNoProxy;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
