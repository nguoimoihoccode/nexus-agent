import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "vitest";
import { fileURLToPath } from "node:url";

const srcDir = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(srcDir, "..");

test("production nginx config does not expose development skill mutation endpoint", () => {
  const config = readFileSync(resolve(frontendDir, "nginx.conf"), "utf-8");

  assert.match(config, /location \/api\//);
  assert.match(config, /NGINX_BACKEND_UPSTREAM/);
  assert.match(config, /Content-Security-Policy/);
  assert.match(config, /object-src 'none'/);
  assert.match(config, /base-uri 'none'/);
  assert.match(config, /script-src-attr 'none'/);
  assert.match(config, /worker-src 'none'/);
  assert.match(config, /manifest-src 'self'/);
  assert.match(config, /X-Content-Type-Options/);
  assert.match(config, /Referrer-Policy/);
  assert.match(config, /Permissions-Policy/);
  assert.match(config, /limit_req zone=nexus_api/);
  assert.match(config, /public, max-age=31536000, immutable/);
  assert.match(config, /\^\/auth\(\?:\/\|\$\) "no-store"/);
  assert.match(config, /Content-Security-Policy-Report-Only/);
  assert.match(config, /Cross-Origin-Opener-Policy "same-origin"/);
  assert.match(config, /Cross-Origin-Resource-Policy "same-origin"/);
  assert.doesNotMatch(config, /\/nexus\/skills/);
});

test("development server is loopback-only unless trusted LAN is explicit", () => {
  const packageJson = readFileSync(resolve(frontendDir, "package.json"), "utf-8");
  const viteConfig = readFileSync(resolve(frontendDir, "vite.config.ts"), "utf-8");

  assert.doesNotMatch(packageJson, /"dev":\s*"[^"]*0\.0\.0\.0/);
  assert.match(viteConfig, /NEXUS_FRONTEND_HOST \|\| "127\.0\.0\.1"/);
  assert.match(viteConfig, /NEXUS_TRUSTED_LAN !== "1"/);
  assert.match(viteConfig, /requires an explicit VITE_API_PROXY_TARGET/);
});

test("production bundle has no browser OIDC token client or runtime token config", () => {
  const packageJson = readFileSync(resolve(frontendDir, "package.json"), "utf-8");
  const indexHtml = readFileSync(resolve(frontendDir, "index.html"), "utf-8");
  const dockerfile = readFileSync(resolve(frontendDir, "Dockerfile"), "utf-8");

  assert.doesNotMatch(packageJson, /oidc-client-ts/);
  assert.doesNotMatch(indexHtml, /runtime-config\.js/);
  assert.doesNotMatch(dockerfile, /runtime-config|NEXUS_OIDC_ISSUER/);
});
