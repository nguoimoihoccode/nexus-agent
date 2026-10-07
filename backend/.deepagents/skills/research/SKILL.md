---
name: research
description: Research source-backed external context for quant workflows without touching data pipelines or experiments.
---

# Research Skill

Use this skill when a task needs source-backed context, documentation lookup,
paper or market-background research, or an explanation of quant concepts and
risks. This skill is read-only.

## Scope

- Use `web_search` for general, current, academic, regulatory, provider, or market
  context.
- Explain quant concepts, data assumptions, metrics, and research risks.
- Synthesize evidence into a concise report with citations.

## Boundaries

- Do not call OpenBB, Qlib, quant-worker, shell commands, notebooks, or raw APIs.
- Do not fetch, create, normalize, validate, or register datasets.
- Do not run, poll, or interpret Qlib experiments or backtests.
- Do not give investment advice or trade recommendations.
- Do not modify files, memory, prompts, skills, docs, or source code.

## Workflow

1. Clarify ambiguity before searching when the topic, market, date range, or
   software/library target is unclear.
2. Search from two useful angles when the claim is non-trivial.
3. Cross-check source freshness and authority before making strong claims.
4. Stop searching once the evidence is enough for the delegated question.

## Output Format

Return findings through the runtime-enforced `ResearchHandoff` contract. Keep
claims linked to declared source IDs, keep source URLs intact, and encode missing
or conflicting evidence as typed evidence gaps.

## Important
- Keep the report under 300 words.
- Use at most four focused `web_search` calls. Never repeat the same query.
- Combine related questions and never repeat identical tool arguments.
- Do NOT include raw search results - synthesize first.
- If live tools are unavailable, state the limitation and do not fabricate citations.
- If asked to ingest data, build a dataset, run Qlib, or analyze experiment results,
  say that the task belongs to the quant data or quant researcher role and provide
  only relevant background context.
