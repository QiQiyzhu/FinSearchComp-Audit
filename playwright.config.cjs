const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './e2e',
  timeout: 45000,
  retries: 0,
  workers: 1,
  use: { browserName: 'chromium', channel: process.env.CI ? undefined : 'msedge', viewport: { width: 1440, height: 1080 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  reporter: [['list'], ['json', {outputFile:'test-results/results.json'}]],
  webServer: [
    { command: 'python -m uvicorn research_workbench.api:create_app --factory --host 127.0.0.1 --port 8091', url: 'http://127.0.0.1:8091/api/health', reuseExistingServer: false, env: { FINAGENT_ENABLE_LIVE: 'false', FINAGENT_DB_PATH: 'build/e2e.sqlite3' } },
    { command: 'python -m http.server 8092 --bind 127.0.0.1 --directory site', url: 'http://127.0.0.1:8092', reuseExistingServer: false }
  ]
});
