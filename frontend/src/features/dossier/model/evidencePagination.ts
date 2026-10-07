export const EVIDENCE_PAGE_SIZE = 10;

export function paginateEvidence<T>(items: readonly T[], requestedPage: number, pageSize = EVIDENCE_PAGE_SIZE) {
  if (!Array.isArray(items)) throw new TypeError("Evidence collection must be an array.");
  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new RangeError("Evidence page size must be a positive integer.");
  }

  const totalItems = items.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const numericPage = Number.isFinite(requestedPage) ? Math.trunc(requestedPage) : 1;
  const page = Math.min(Math.max(numericPage, 1), totalPages);
  const startIndex = (page - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, totalItems);

  return {
    items: items.slice(startIndex, endIndex),
    page,
    pageSize,
    totalItems,
    totalPages,
    startItem: totalItems === 0 ? 0 : startIndex + 1,
    endItem: endIndex,
  };
}
