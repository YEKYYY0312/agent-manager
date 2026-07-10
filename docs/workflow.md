# Collaboration Workflow

## Ownership

| Phase | Deliverable | Owner | Output |
|---|---|---|---|
| Phase 0 | Product contract and trace schema | User + Codex | `docs/product-spec.md`, `docs/trace-schema.md`, `schemas/trace.schema.json` |
| Phase 1 | Project skeleton | Claude Code + Codex | Repository layout, README, package folders |
| Phase 2 | Core tracing engine | Claude Code | Python SDK, trace writer, decorators, run context |
| Phase 3 | CLI tool | Codex | Trace list/show/inspect/cost/diff commands |
| Phase 4 | Analysis module | Claude Code | Cost, latency, loop, failure, and retry analysis |
| Phase 5 | Web UI | Claude Code + Codex | Timeline, inspector, run diff, cost views |
| Phase 6 | Tests and docs | User + Claude Code + Codex | Demo traces, examples, README, test matrix |

## Interface Contract

All contributors should treat `schemas/trace.schema.json` as the source of truth. If a feature needs new fields, update the schema and `docs/trace-schema.md` before implementation.

## Branching Rule

Keep each phase small and reviewable. A phase should leave the project runnable even if later phases are incomplete.

## Handoff Rule

Every handoff should include:

1. What changed.
2. Which files define the interface.
3. How to verify the result.
4. Known gaps or next steps.
