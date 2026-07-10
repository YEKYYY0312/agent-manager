# Product Spec

## Product Name

Agent DevTools

## One-line Pitch

A local-first debugger that records, inspects, replays, and compares AI agent runs.

## Target Users

- Developers building production AI agents.
- Teams using LangGraph, CrewAI, OpenAI Agents SDK, or custom Python agents.
- Internal AI platform teams that need cost, failure, and behavior visibility.

## Primary Problem

Agent failures are hard to explain. A run may include hidden planning, multiple LLM calls, tool calls, retrieval steps, retries, state changes, and memory operations. When output quality drops or cost spikes, developers often cannot tell which step caused the issue.

## MVP User Story

As an agent developer, I want to open one failed run, see every decision and tool call in order, understand where cost and latency were spent, then compare it with a successful run.

## MVP Features

1. Trace timeline: ordered steps and events for each run.
2. Step inspector: input, output, model, tool args, tool result, cost, latency, and errors.
3. Cost summary: total cost, token usage, model calls, slowest steps, and expensive steps.
4. Run diff: compare two runs by steps, status, cost, latency, and final output.
5. Experiment comparison: compare two runs as A/B variants with transparent success/cost/latency rules.
6. Privacy scan and redaction: detect sensitive trace locations and create sanitized trace copies before sharing, storing, or exporting.
7. Local persistence: persist imported browser traces and optionally index traces in SQLite.
8. Portable trace format: JSON file readable by CLI, Web UI, and analysis modules.
9. OpenTelemetry export: convert local trace files into OTLP JSON or push them to an OTLP HTTP endpoint for external observability workflows.

## Non-goals For MVP

- Hosted SaaS account system.
- Team permissions and RBAC.
- Hosted OpenTelemetry ingestion pipeline.
- Hosted long-term trace storage service.
- Automatic root-cause AI diagnosis.
- Support for every agent framework.

## Success Criteria

- A developer can inspect a sample run from the CLI in under one minute.
- Two traces can be compared without custom scripts.
- Claude Code can implement the tracing SDK using the schema without ambiguity.
- The Web UI renders timeline, analysis, diff, replay planning, and experiment comparison directly from the same trace file.
- A developer can redact and share a trace without exposing common secret fields.
