const NEXUS_PREFIX = "nexus-";
const OIDC_PREFIX = "oidc.";

function clearMatching(storage: Storage, includeOidc: boolean): void {
  const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index))
    .filter((key): key is string => Boolean(key))
    .filter((key) => key.startsWith(NEXUS_PREFIX) || (includeOidc && key.startsWith(OIDC_PREFIX)));
  keys.forEach((key) => storage.removeItem(key));
}

export function clearNexusBrowserState(includeOidc = false): void {
  if (typeof window === "undefined") return;
  clearMatching(window.sessionStorage, includeOidc);
  clearMatching(window.localStorage, includeOidc);
}
