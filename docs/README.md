# Nexus Agent Docs

Source code and executable configuration are authoritative. Documentation covers
only the supported local-development and single-node private-beta product.

## Document map

| Type | Use it for | Documents |
| --- | --- | --- |
| Architecture | Runtime boundaries, ownership, durable decisions | [System overview](architecture/overview.md) |
| Guides | Setup, checks, and evidence of workflow stability | [Development](guides/development.md), [Product validation](guides/product-validation.md) |
| Workflows | Supported business flows and agent boundaries | [Quant research](workflows/quant-research.md), [AI-Trader](workflows/ai-trader.md) |
| Reference | Machine-readable cross-service fixtures | [Canonical identity v1](reference/canonical-identity-v1.json) |
| PDF | Bản tổng quan toàn hệ thống bằng tiếng Việt, có ví dụ nhập môn | [Nexus Agent — Toàn cảnh hệ thống](pdf/nexus-agent-toan-canh-he-thong-vi.pdf) |

## Repository map

```text
backend/             LangGraph supervisor, agents, product APIs, PostgreSQL state
frontend/            React chat, capabilities, memory, and experiment evidence UI
quant-data-worker/   OpenBB ingestion and immutable dataset revisions
quant-worker/        Qlib experiment jobs and verified local artifacts
ai-trader-worker/    Cached market intelligence and publication adapter
evaluation/          Versioned deterministic product-validation manifest
scripts/             Architecture, validation, and security gates
```

Keep pages narrow: architecture explains ownership, guides explain how to operate or
verify it, and workflows explain supported product behavior. Update the existing page
for its category instead of creating an overlapping document.
