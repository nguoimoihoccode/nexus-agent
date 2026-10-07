import { type ElementType, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { externalHostname, safeHref } from "./markdownUtils.js";

const EXPERIMENT_ID = /^exp_v1_[0-9a-f]{32}$/;

type MarkdownBlock =
  | { type: "code"; language?: string; text: string }
  | { type: "heading"; level: number; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] }
  | { type: "quote"; text: string }
  | { type: "paragraph"; text: string };

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(([^)\s]+)\)|\*[^*]+\*|(?<![A-Za-z0-9_])exp_v1_[0-9a-f]{32}(?![A-Za-z0-9_]))/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) parts.push(text.slice(cursor, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;

    if (token.startsWith("`")) {
      const code = token.slice(1, -1);
      parts.push(EXPERIMENT_ID.test(code)
        ? <Link key={key} className="experiment-inline-link" to="/evidence/$experimentId" params={{ experimentId: code }}><code>{code}</code></Link>
        : <code key={key}>{code}</code>);
    } else if (token.startsWith("**")) {
      parts.push(<strong key={key}>{renderInline(token.slice(2, -2), `${key}-strong`)}</strong>);
    } else if (token.startsWith("*")) {
      parts.push(<em key={key}>{renderInline(token.slice(1, -1), `${key}-em`)}</em>);
    } else if (EXPERIMENT_ID.test(token)) {
      parts.push(<Link key={key} className="experiment-inline-link" to="/evidence/$experimentId" params={{ experimentId: token }}>{token}</Link>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
      const href = safeHref(link?.[2] || "#");
      const hostname = externalHostname(href);
      parts.push(
        <a key={key} href={href} target="_blank" rel="noopener noreferrer">
          {link?.[1] || token}
          {hostname && <small className="external-link-host">{hostname}</small>}
        </a>,
      );
    }
    cursor = match.index + token.length;
  }

  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

function renderLines(text: string, keyPrefix: string): ReactNode[] {
  return text.split("\n").flatMap((line, index, lines) => {
    const rendered = renderInline(line, `${keyPrefix}-${index}`);
    return index < lines.length - 1
      ? [...rendered, <br key={`${keyPrefix}-br-${index}`} />]
      : rendered;
  });
}

export function MarkdownResponse({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```(\w+)?\s*$/);
    if (fence) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index] ?? "")) {
        code.push(lines[index] ?? "");
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push({ type: "code", ...(fence[1] ? { language: fence[1] } : {}), text: code.join("\n") });
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1]?.length || 1, text: heading[2] || "" });
      index += 1;
      continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      const orderedList = Boolean(ordered);
      const items: string[] = [];
      while (index < lines.length) {
        const item = orderedList
          ? (lines[index] ?? "").match(/^\s*\d+\.\s+(.+)$/)
          : (lines[index] ?? "").match(/^\s*[-*+]\s+(.+)$/);
        if (!item) break;
        items.push(item[1] || "");
        index += 1;
      }
      blocks.push({ type: orderedList ? "ol" : "ul", items });
      continue;
    }

    const quote = line.match(/^\s*>\s?(.+)$/);
    if (quote) {
      const quotes: string[] = [];
      while (index < lines.length) {
        const item = (lines[index] ?? "").match(/^\s*>\s?(.+)$/);
        if (!item) break;
        quotes.push(item[1] || "");
        index += 1;
      }
      blocks.push({ type: "quote", text: quotes.join("\n") });
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (
      index < lines.length
      && (lines[index] ?? "").trim()
      && !/^```/.test(lines[index] ?? "")
      && !/^(#{1,3})\s+/.test(lines[index] ?? "")
      && !/^\s*([-*+]|\d+\.)\s+/.test(lines[index] ?? "")
      && !/^\s*>/.test(lines[index] ?? "")
    ) {
      paragraph.push(lines[index] ?? "");
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join("\n") });
  }

  if (!blocks.length) return null;

  return (
    <div className="markdown-response">
      {blocks.map((block, blockIndex) => {
        const key = `md-${blockIndex}`;
        if (block.type === "code") {
          return (
            <pre key={key}>
              {block.language && <span>{block.language}</span>}
              <code>{block.text}</code>
            </pre>
          );
        }
        if (block.type === "heading") {
          const HeadingTag = `h${block.level + 2}` as ElementType;
          return <HeadingTag key={key}>{renderInline(block.text, key)}</HeadingTag>;
        }
        if (block.type === "ul" || block.type === "ol") {
          const ListTag = block.type;
          return (
            <ListTag key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ListTag>
          );
        }
        if (block.type === "quote") {
          return <blockquote key={key}>{renderLines(block.text, key)}</blockquote>;
        }
        return <p key={key}>{renderLines(block.text, key)}</p>;
      })}
    </div>
  );
}
