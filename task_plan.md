# Agent DevTools Completion Plan

**Goal:** Deliver the remaining P0-P2 capabilities from the agreed priority table, then publish the validated result to GitHub Pages.

## Phases

- [x] P0 local workbench: local initialization, sample Trace, loopback API, Web UI discovery, MCP query tools, and explicit external audit records.
- [x] P0 distribution and Codex setup: verified isolated pipx installation, registered the local Codex MCP server, and added a CI package smoke check.
- [x] P1 TypeScript SDK and CI template: provide a schema-compatible Node/TypeScript writer and a reusable GitHub Actions regression workflow.
- [x] P1 team service: add a self-hosted Postgres-backed API with token roles, retention controls, and indexed trace search.
- [x] P2 evaluation: add datasets, deterministic quality rubrics, annotation records, failure clustering, and batch CLI commands.
- [ ] Release: run complete verification, commit all work, push, merge, and verify GitHub Pages deployment.

## Constraints

- Preserve the existing local-first JSON Trace contract and redaction defaults.
- Keep local SQLite as the zero-configuration default.
- Do not claim to collect Codex hidden reasoning or platform telemetry.
- Do not require a hosted vendor; production team deployment remains self-hostable.
- Every behavior change receives a focused test before implementation.

## Current Focus

P1 TypeScript SDK and CI template, beginning with a dependency-free schema-compatible trace writer.
