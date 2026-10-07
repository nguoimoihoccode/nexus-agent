import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "vitest";
import { fileURLToPath } from "node:url";

const layoutDir = dirname(fileURLToPath(import.meta.url));
const srcDir = resolve(layoutDir, "..");
const styles = [
  readFileSync(resolve(srcDir, "styles", "base.css"), "utf-8"),
  readFileSync(resolve(srcDir, "styles.css"), "utf-8"),
].join("\n");
const app = readFileSync(resolve(srcDir, "App.tsx"), "utf-8");

test("keeps the product shell bounded while product main owns vertical scrolling", () => {
  assert.match(styles, /html, body, #root \{[^}]*height: 100%;[^}]*overflow: hidden;/);
  assert.match(styles, /\.product-shell \{[^}]*height: 100dvh;[^}]*overflow: hidden;/);
  assert.match(styles, /\.product-main \{[^}]*height: 100dvh;[^}]*overflow-y: auto;/);
  assert.match(styles, /\.product-content \{[^}]*flex: none;[^}]*min-height: calc\(100% - 72px\);/);
});

test("prevents the desktop topbar from shrinking on long pages", () => {
  assert.match(styles, /\.product-topbar \{[^}]*height: 72px;[^}]*flex: 0 0 72px;/);
});

test("resets both desktop and responsive scroll viewports during navigation", () => {
  assert.ok(app.includes('document.querySelector(".product-main")?.scrollTo({ top: 0, left: 0, behavior: "auto" });'));
  assert.ok(app.includes('window.scrollTo({ top: 0, left: 0, behavior: "auto" });'));
});
