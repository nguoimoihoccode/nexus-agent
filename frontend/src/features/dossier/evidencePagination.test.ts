import assert from "node:assert/strict";
import { test } from "vitest";

import { EVIDENCE_PAGE_SIZE, paginateEvidence } from "./model/evidencePagination.js";

test("paginateEvidence returns an immutable first-page window", () => {
  const source = Array.from({ length: 23 }, (_, index) => ({ id: index + 1 }));
  const page = paginateEvidence(source, 1);

  assert.deepEqual(page.items.map((item) => item.id), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.equal(page.pageSize, EVIDENCE_PAGE_SIZE);
  assert.equal(page.totalItems, 23);
  assert.equal(page.totalPages, 3);
  assert.equal(page.startItem, 1);
  assert.equal(page.endItem, 10);
  assert.equal(source.length, 23);
});

test("paginateEvidence returns the final partial window", () => {
  const page = paginateEvidence(Array.from({ length: 23 }, (_, index) => index + 1), 3);

  assert.deepEqual(page.items, [21, 22, 23]);
  assert.equal(page.startItem, 21);
  assert.equal(page.endItem, 23);
});

test("paginateEvidence clamps stale and invalid page requests", () => {
  const items = Array.from({ length: 12 }, (_, index) => index);

  assert.equal(paginateEvidence(items, 99).page, 2);
  assert.equal(paginateEvidence(items, -4).page, 1);
  assert.equal(paginateEvidence(items, Number.NaN).page, 1);
});

test("paginateEvidence represents an empty collection without phantom item indexes", () => {
  assert.deepEqual(paginateEvidence([], 4), {
    items: [],
    page: 1,
    pageSize: EVIDENCE_PAGE_SIZE,
    totalItems: 0,
    totalPages: 1,
    startItem: 0,
    endItem: 0,
  });
});

test("paginateEvidence rejects malformed collection and page-size inputs", () => {
  assert.throws(() => paginateEvidence(null, 1), TypeError);
  assert.throws(() => paginateEvidence([], 1, 0), RangeError);
  assert.throws(() => paginateEvidence([], 1, 1.5), RangeError);
});
