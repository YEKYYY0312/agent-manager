# Trace Schema

## Purpose

The trace schema is the project contract. The SDK writes it, the CLI reads it, the analysis module enriches it, and the Web UI renders it.

## Core Concepts

### Trace

A complete agent run saved as one JSON file.

### Run

Top-level metadata: id, task, status, start/end time, duration, labels, and final output.

### Step

A major unit in the run timeline, such as an LLM call, tool call, retrieval, memory operation, planning step, or control step.

### Event

A lower-level timestamped record inside a step. Events are optional in MVP but allow richer playback later.

### Cost

Token and money accounting. Cost may exist at run level and step level.

### Replay Point

A step that can be used as a replay fork point. The CLI can generate deterministic replay traces from this marker, and replay comparison can compare the replay against the original source path.

## Step Types

- `model_call`
- `tool_call`
- `retrieval`
- `memory`
- `planner`
- `control`
- `custom`

## Status Values

- `success`
- `error`
- `cancelled`
- `timeout`

## Required MVP Fields

At minimum, every trace file must contain:

- `schema_version`
- `run.id`
- `run.task`
- `run.status`
- `run.started_at`
- `steps[]`
- each step `id`, `type`, `name`, `status`, `started_at`

## Compatibility Rule

Additive fields are allowed. Renaming or removing fields requires a schema version change.
