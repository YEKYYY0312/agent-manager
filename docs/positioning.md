# Product Positioning

## Position

Agent DevTools is a local-first debugging workbench for AI agent development. It records agent runs as portable `.trace.json` files, then lets one developer inspect, analyze, compare, privacy-scan, redact, persist, export, and replay those traces without creating a cloud account or running a data platform.

## What It Is

- A local trace recorder for Python agent code.
- A CLI for inspecting, analyzing, diffing, privacy-scanning, redacting, replaying, experimenting, OTLP-exporting/pushing, and storing traces.
- A Web UI for timeline inspection, step details, analysis, run diff, replay planning, replay comparison, experiment comparison, and persisted local imports.
- A trace-file-first workflow that works well for Claude Code, Codex, custom scripts, demos, and early agent prototypes.

## What It Is Not

- Not a hosted observability SaaS.
- Not a team permission system.
- Not a replacement for production telemetry platforms.
- Not yet a full framework-specific runtime replay engine.

## Differentiation

The project should not claim that nobody is building agent observability. The useful wedge is narrower and clearer:

> Local-first Agent DevTools for developers who want to debug one run, compare two runs, and share a sanitized trace without adopting a hosted platform.

This means the product should optimize for:

- zero-account local usage
- readable JSON traces
- fast CLI workflows
- Chinese-friendly Web UI labels
- explicit privacy scan/redaction before sharing, storing, or exporting
- optional local SQLite indexing instead of mandatory server storage

## Current Product Boundary

The current product is a complete local MVP. It can record, inspect, analyze, diff, privacy-scan, redact, replay deterministically with optional edited tool mocks, execute explicit CLI callable adapter replay, compare original runs against replay runs, run adapter-based Python callable, LangGraph graph/node-level, OpenAI Responses/Chat, Anthropic Messages, and Anthropic local tool-use-loop replay, compare experiments, persist imported browser traces, index traces in SQLite, export OTLP JSON, and push OTLP HTTP JSON to a Collector endpoint.

Deeper framework-specific adapters are the next major boundary. They should remain optional adapters that keep the trace schema stable instead of turning the project into a framework-specific platform.
