# Changelog

All notable changes to Agent DevTools are documented here.

This project follows a simple pre-1.0 versioning rule:

- Patch releases fix bugs, security defaults, docs, and packaging.
- Minor releases add trace schema-compatible features.
- Breaking trace schema changes require an explicit migration note.

## 0.1.0 - MVP

Initial local-first Agent DevTools MVP.

### Added

- Python SDK for recording agent runs as `.trace.json` files.
- Decorators and context managers for model, tool, planner, retrieval, memory, control, and custom steps.
- CLI commands for trace inspection, analysis, diffing, deterministic replay, adapter replay, replay comparison, experiment comparison, privacy scanning, redaction, SQLite storage, regression checks, OTLP JSON export, and OTLP HTTP push.
- Web UI workbench for timeline inspection, step details, analysis, run diff, replay planning, replay comparison, and experiment comparison.
- Optional callable, LangGraph, OpenAI, and Anthropic adapters.
- Local-first privacy controls including secret scanning and redaction.
- GitHub Actions CI for Python SDK, CLI, and Web UI checks.

### Security

- Sensitive trace exports and storage paths run privacy preflight checks by default.
- Local callable replay requires explicit `--allow-unsafe-code`.
- OTLP push blocks non-loopback insecure/private endpoints unless explicitly allowed.
- Trace loading and writing enforce size, depth, cost, path, and event-count limits.

