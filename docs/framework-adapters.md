# Framework Adapters

Agent DevTools keeps framework integration behind a small adapter contract. The core SDK stays local-first and dependency-free; LangGraph, OpenAI SDK, Anthropic SDK, Claude Code, or Codex integrations can be added as optional wrappers without changing the trace JSON schema.

## Adapter Contract

An adapter executes an agent and returns both the agent output and the trace recorded during that execution.

```python
from typing import Any

from agent_devtools import AdapterRunResult


class AgentAdapter:
    name: str

    def run(
        self,
        task: str,
        input: Any = None,
        *,
        labels: dict[str, str] | None = None,
        output_dir: str = "traces",
    ) -> AdapterRunResult:
        ...
```

The returned `AdapterRunResult` contains:

- `output`: the agent output, or `None` when execution failed.
- `trace`: the newly recorded `Trace`.
- `error`: the captured trace error, or `None` on success.

## Callable Adapter

Use `CallableAgentAdapter` for custom Python agents or quick framework experiments:

```python
from agent_devtools import CallableAgentAdapter, replay_with_adapter


def agent(payload):
    return {"answer": f"received {payload}"}


adapter = CallableAgentAdapter(agent, name="demo-agent")
result = adapter.run(
    task="Answer a question",
    input={"question": "weather"},
    output_dir="traces",
)

print(result.output)
print(result.trace.run.id)
```

The callable receives one argument: the task or replay input payload. If the callable raises an exception, the adapter returns an `AdapterRunResult` with `error` set and writes a failed trace.

## LangGraph Adapter

Use `LangGraphAdapter` with a compiled LangGraph graph. By default the adapter calls `graph.invoke(...)`, records the invocation as one trace step, and keeps LangGraph optional instead of making it a core package dependency.

```python
from agent_devtools import LangGraphAdapter


# agent = StateGraph(...).compile()
adapter = LangGraphAdapter(
    agent,
    name="qa-graph",
    config={"configurable": {"thread_id": "demo-thread"}},
)

result = adapter.run(
    task="Answer with LangGraph",
    input={"messages": messages},
    output_dir="traces",
)
```

For node-level streaming, enable `trace_stream=True`:

```python
adapter = LangGraphAdapter(
    agent,
    name="qa-graph",
    trace_stream=True,
)

result = adapter.run(
    task="Answer with LangGraph",
    input={"messages": messages},
    output_dir="traces",
)
```

Streaming mode calls `graph.stream(..., stream_mode="updates", version="v2")` and records each emitted node update as a separate trace step. The final adapter output is the collected node outputs keyed by node name.

## OpenAI Adapter

Use `OpenAIAdapter` with an OpenAI Python SDK client. The adapter is optional and uses duck typing, so the core package does not import or require `openai`.

Responses API:

```python
from openai import OpenAI
from agent_devtools import OpenAIAdapter


client = OpenAI()
adapter = OpenAIAdapter(
    client,
    model="gpt-4.1-mini",
    name="openai-weather",
)

result = adapter.run(
    task="Ask OpenAI",
    input="weather in Shanghai",
    output_dir="traces",
)
```

To expand Responses `output` items into child steps, enable `expand_output_items=True`:

```python
adapter = OpenAIAdapter(
    client,
    model="gpt-4.1-mini",
    name="openai-weather",
    expand_output_items=True,
)
```

When enabled, the main `responses.create` step remains in the trace. Response output items such as `message`, `file_search_call`, and `function_call` are recorded as child steps with `parent_id` pointing at the main OpenAI call. Function-call arguments are parsed from JSON strings when possible so they are easier to inspect and diff.

Chat Completions:

```python
adapter = OpenAIAdapter(
    client,
    model="gpt-4o-mini",
    endpoint="chat.completions",
    name="openai-chat",
)

result = adapter.run(
    task="Ask Chat Completions",
    input="weather in Shanghai",
    output_dir="traces",
)
```

The adapter records a `model_call` step and maps `response.usage` into `Cost` when token usage is available. Responses output prefers `response.output_text`; Chat Completions output prefers the first choice message content. Output item expansion is currently available for the Responses API endpoint only.

## Anthropic Adapter

Use `AnthropicAdapter` with an Anthropic Python SDK client. The adapter is optional and uses duck typing, so the core package does not import or require `anthropic`.

```python
from anthropic import Anthropic
from agent_devtools import AnthropicAdapter


client = Anthropic()
adapter = AnthropicAdapter(
    client,
    model="claude-opus-4-8",
    name="claude-weather",
)

result = adapter.run(
    task="Ask Claude",
    input="weather in Shanghai",
    output_dir="traces",
)
```

To pass an existing conversation, provide `{"messages": [...]}`:

```python
result = adapter.run(
    task="Continue conversation",
    input={"messages": [{"role": "user", "content": "hello"}]},
)
```

To expand Claude Messages `content` blocks into child steps, enable `expand_content_blocks=True`:

```python
adapter = AnthropicAdapter(
    client,
    model="claude-opus-4-8",
    name="claude-weather",
    expand_content_blocks=True,
)
```

When enabled, the main `messages.create` step remains in the trace. `text` blocks are recorded as `model_call` child steps, `thinking` blocks as `planner` child steps, and `tool_use` blocks as `tool_call` child steps with `tool.args` populated from the structured tool input.

The adapter records a `model_call` step and maps `response.usage` into `Cost` when token usage is available. It extracts text from `response.content` text blocks. Tool-use loop execution and streaming event expansion are planned follow-ups.

## Adapter Replay

`create_replay_trace(...)` remains deterministic: it reuses recorded tool/model outputs and does not call real agents.

`replay_with_adapter(...)` executes a real adapter:

```python
from agent_devtools import CallableAgentAdapter, Trace, replay_with_adapter


source = Trace.from_file("traces/source.trace.json")
adapter = CallableAgentAdapter(lambda payload: {"answer": payload}, name="demo-agent")

result = replay_with_adapter(
    source,
    start_step_id="<step-id>",
    adapter=adapter,
    output_dir="traces",
)
```

If no explicit replay input is provided, Agent DevTools uses the selected step's `input`. For tool steps, it falls back to `tool.args`.

The CLI exposes explicit callable adapter replay with `replay-adapter`:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\source.trace.json --start-step <step-id> --callable path\to\agent.py:run --allow-unsafe-code --output-dir traces
```

This command executes local Python code only after `--callable` and `--allow-unsafe-code` are provided. The callable path can be `module:function` or `path\to\file.py:function`. Use `--input-json '{"question":"override"}'` to override the recorded step input.

After creating a replay trace, compare it against the source trace:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-compare traces\source.trace.json traces\replay.trace.json
```

The comparison reads `source_run_id`, `source_start_step_id`, and `replay_mode` labels from the replay trace, then reports status, output, token, cost, latency, step-count, and per-step drift.

The new run is labeled with:

- `replay=true`
- `replay_mode=adapter_execution`
- `source_run_id`
- `source_start_step_id`
- `source_run_status`
- `adapter`

## Planned Specific Adapters

Specific adapters should stay optional and preserve the same contract:

- LangGraph: graph-level `invoke` and node-level streaming update traces are implemented.
- OpenAI SDK: Responses API and Chat Completions adapters are implemented; Responses output item expansion is implemented; Agents SDK tracing and streaming event expansion are planned.
- Anthropic SDK: Messages API adapter and content block expansion are implemented; tool-use loop execution and streaming event expansion are planned.
- Claude Code/Codex: import or bridge local execution logs into trace runs where possible.

These adapters should not make the core package depend on those frameworks by default.
