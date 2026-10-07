export function safeHref(href: string): string {
  const value = String(href || "").trim();
  if (/^(https:|mailto:)/i.test(value)) return value;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  if (value.startsWith("#")) return value;
  return "#";
}

export function externalHostname(href: string): string | null {
  try {
    const url = new URL(safeHref(href));
    return url.protocol === "https:" ? url.hostname : null;
  } catch {
    return null;
  }
}
