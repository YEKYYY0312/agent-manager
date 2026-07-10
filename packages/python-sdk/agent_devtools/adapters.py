"""Adapter contracts for executing real agent runtimes under tracing."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .context import TraceContext
from .decorators import _compute_usd
from .trace import Cost, Error, ToolCall, Trace

_FORBIDDEN_REQUEST_OPTION_KEYS = {
    "api_key",
    "api-key",
    "apikey",
    "base_url",
    "base-url",
    "base_uri",
    "base-uri",
    "organization",
    "default_headers",
    "http_client",
    "timeout",
}


@dataclass
class AdapterRunResult:
    """Result returned by an agent adapter execution."""

    output: Any
    trace: Trace
    error: Error | None = None


class AgentAdapter(Protocol):
    """Protocol implemented by framework-specific agent adapters."""

    name: str

    def run(
        self,
        task: str,
        input: Any = None,
        *,
        labels: dict[str, str] | None = None,
        output_dir: str = "traces",
    ) -> AdapterRunResult:
        """Execute an agent and return the output plus the recorded trace."""


class CallableAgentAdapter:
    """Run a plain Python callable as a traced agent.

    The callable receives one argument: the replay or task input payload. More
    specific framework adapters can build on this same contract without adding
    framework dependencies to the core SDK.
    """

    def __init__(self, fn: Callable[[Any], Any], name: str | None = None) -> None:
        self.fn = fn
        self.name = name or _callable_name(fn)

    def run(
        self,
        task: str,
        input: Any = None,
        *,
        labels: dict[str, str] | None = None,
        output_dir: str = "traces",
    ) -> AdapterRunResult:
        run_labels = _labels(labels)
        run_labels["adapter"] = self.name
        run_labels["adapter_type"] = "callable"

        with TraceContext(task=task, labels=run_labels, output_dir=output_dir) as ctx:
            output = None
            error = None
            try:
                with ctx.step("custom", f"{self.name}.run", input=input, replayable=False) as step:
                    step.metadata["adapter"] = self.name
                    step.metadata["adapter_type"] = "callable"
                    output = self.fn(input)
                    step.complete(status="success", output=output)
            except Exception as exc:
                output = None
                error = Error.from_exc(exc)
                ctx.trace.run.complete(status=_status_for_exception(exc), final_output=error.message)
            else:
                ctx.trace.run.complete(status="success", final_output=output)

            return AdapterRunResult(output=output, trace=ctx.trace, error=error)


class LangGraphAdapter:
    """Run a compiled LangGraph graph through the common adapter contract.

    The adapter intentionally uses duck typing and calls ``graph.invoke`` so the
    core SDK does not depend on LangGraph at install time.
    """

    def __init__(
        self,
        graph: Any,
        name: str = "langgraph",
        config: dict[str, Any] | None = None,
        trace_stream: bool = False,
        stream_mode: str = "updates",
        stream_version: str | None = "v2",
    ) -> None:
        invoke = getattr(graph, "invoke", None)
        if not callable(invoke):
            raise TypeError("LangGraphAdapter requires a graph object with an invoke method")
        if trace_stream and not callable(getattr(graph, "stream", None)):
            raise TypeError("LangGraphAdapter trace_stream=True requires a graph object with a stream method")
        self.graph = graph
        self.name = name
        self.config = config
        self.trace_stream = trace_stream
        self.stream_mode = stream_mode
        self.stream_version = stream_version

    def run(
        self,
        task: str,
        input: Any = None,
        *,
        labels: dict[str, str] | None = None,
        output_dir: str = "traces",
        config: dict[str, Any] | None = None,
        trace_stream: bool | None = None,
        stream_mode: str | None = None,
        stream_version: str | None = None,
    ) -> AdapterRunResult:
        run_labels = _labels(labels)
        run_labels["adapter"] = self.name
        run_labels["adapter_type"] = "langgraph"

        effective_config = self.config if config is None else config
        effective_trace_stream = self.trace_stream if trace_stream is None else trace_stream
        effective_stream_mode = stream_mode or self.stream_mode
        effective_stream_version = self.stream_version if stream_version is None else stream_version

        if effective_trace_stream:
            run_labels["langgraph_stream"] = "true"

        with TraceContext(task=task, labels=run_labels, output_dir=output_dir) as ctx:
            output = None
            error = None
            try:
                if effective_trace_stream:
                    output = self._run_stream(
                        ctx=ctx,
                        input=input,
                        config=effective_config,
                        stream_mode=effective_stream_mode,
                        stream_version=effective_stream_version,
                    )
                else:
                    with ctx.step("custom", f"{self.name}.invoke", input=input, replayable=False) as step:
                        step.metadata["adapter"] = self.name
                        step.metadata["adapter_type"] = "langgraph"
                        if effective_config is not None:
                            step.metadata["langgraph_config"] = effective_config
                        if effective_config is None:
                            output = self.graph.invoke(input)
                        else:
                            output = self.graph.invoke(input, config=effective_config)
                        step.complete(status="success", output=output)
            except Exception as exc:
                error = Error.from_exc(exc)
                ctx.trace.run.complete(status=_status_for_exception(exc), final_output=error.message)
            else:
                ctx.trace.run.complete(status="success", final_output=output)

            return AdapterRunResult(output=output, trace=ctx.trace, error=error)

    def _run_stream(
        self,
        ctx: TraceContext,
        input: Any,
        config: dict[str, Any] | None,
        stream_mode: str,
        stream_version: str | None,
    ) -> Any:
        stream = getattr(self.graph, "stream", None)
        if not callable(stream):
            raise TypeError("LangGraphAdapter trace_stream=True requires a graph object with a stream method")

        kwargs: dict[str, Any] = {"stream_mode": stream_mode}
        if config is not None:
            kwargs["config"] = config
        if stream_version is not None:
            kwargs["version"] = stream_version

        output_by_node: dict[str, Any] = {}
        last_output = None
        chunk_index = 0

        for chunk in stream(input, **kwargs):
            for event in _langgraph_stream_events(chunk, stream_mode=stream_mode, stream_index=chunk_index):
                node_name = event["node"]
                with ctx.step("custom", f"{self.name}.{node_name}", replayable=False) as step:
                    step.metadata["adapter"] = self.name
                    step.metadata["adapter_type"] = "langgraph"
                    step.metadata["langgraph_stream"] = True
                    step.metadata["langgraph_stream_mode"] = stream_mode
                    step.metadata["langgraph_stream_chunk_type"] = event["chunk_type"]
                    step.metadata["langgraph_stream_index"] = chunk_index
                    step.metadata["langgraph_node"] = node_name
                    if event["namespace"]:
                        step.metadata["langgraph_namespace"] = event["namespace"]
                    if config is not None:
                        step.metadata["langgraph_config"] = config
                    step.complete(status="success", output=event["output"])

                output_by_node[node_name] = deepcopy(event["output"])
                last_output = deepcopy(event["output"])
                chunk_index += 1

        return output_by_node if output_by_node else last_output


class OpenAIAdapter:
    """Run OpenAI SDK calls through the common adapter contract.

    The adapter uses duck typing so the core package does not depend on the
    OpenAI Python SDK. It supports the Responses API by default and Chat
    Completions when ``endpoint="chat.completions"``.
    """

    _SUPPORTED_ENDPOINTS = {"responses", "chat.completions"}

    def __init__(
        self,
        client: Any,
        model: str,
        name: str = "openai",
        endpoint: str = "responses",
        request_options: dict[str, Any] | None = None,
        track_cost: bool = True,
        expand_output_items: bool = False,
    ) -> None:
        if endpoint not in self._SUPPORTED_ENDPOINTS:
            raise ValueError(f"Unsupported OpenAI endpoint: {endpoint}")
        self.client = client
        self.model = model
        self.name = name
        self.endpoint = endpoint
        self.request_options = _safe_request_options(request_options)
        self.track_cost = track_cost
        self.expand_output_items = expand_output_items

    def run(
        self,
        task: str,
        input: Any = None,
        *,
        labels: dict[str, str] | None = None,
        output_dir: str = "traces",
        request_options: dict[str, Any] | None = None,
        expand_output_items: bool | None = None,
    ) -> AdapterRunResult:
        run_labels = _labels(labels)
        run_labels["adapter"] = self.name
        run_labels["adapter_type"] = "openai"
        run_labels["openai_endpoint"] = self.endpoint

        effective_expand_output_items = self.expand_output_items if expand_output_items is None else expand_output_items
        if effective_expand_output_items:
            run_labels["openai_expand_output_items"] = "true"

        options = dict(self.request_options)
        if request_options:
            options.update(request_options)

        step_input = _openai_step_input(self.endpoint, input)

        with TraceContext(task=task, labels=run_labels, output_dir=output_dir) as ctx:
            output = None
            error = None
            try:
                with ctx.step(
                    "model_call",
                    f"{self.name}.{self.endpoint}.create",
                    input=step_input,
                    model=self.model,
                    replayable=False,
                ) as step:
                    step.metadata["adapter"] = self.name
                    step.metadata["adapter_type"] = "openai"
                    step.metadata["openai_endpoint"] = self.endpoint
                    response = self._create(input=input, options=options)
                    output = _openai_output(response)
                    response_id = _value(response, "id")
                    if response_id:
                        step.metadata["openai_response_id"] = response_id
                    cost = _openai_cost(response, model=self.model) if self.track_cost else None
                    step.complete(status="success", output=output, cost=cost)
                    if effective_expand_output_items and self.endpoint == "responses":
                        _record_openai_output_items(ctx, adapter_name=self.name, response=response)
            except Exception as exc:
                error = Error.from_exc(exc)
                ctx.trace.run.complete(status=_status_for_exception(exc), final_output=error.message)
            else:
                ctx.trace.run.complete(status="success", final_output=output)

            return AdapterRunResult(output=output, trace=ctx.trace, error=error)

    def _create(self, input: Any, options: dict[str, Any]) -> Any:
        if self.endpoint == "responses":
            create = _create_method(self.client, ["responses", "create"], endpoint="responses")
            return create(model=self.model, input=input, **options)

        create = _create_method(self.client, ["chat", "completions", "create"], endpoint="chat.completions")
        messages = _chat_messages(input)
        return create(model=self.model, messages=messages, **options)


class AnthropicAdapter:
    """Run Anthropic Messages API calls through the common adapter contract.

    The adapter uses duck typing so the core package does not depend on the
    Anthropic Python SDK. Pass an ``anthropic.Anthropic()``-style client whose
    ``messages.create`` method matches the official SDK surface.
    """

    def __init__(
        self,
        client: Any,
        model: str = "claude-opus-4-8",
        name: str = "anthropic",
        request_options: dict[str, Any] | None = None,
        track_cost: bool = True,
        expand_content_blocks: bool = False,
        tools: dict[str, Callable[..., Any]] | None = None,
        max_tool_rounds: int = 4,
    ) -> None:
        _create_method(client, ["messages", "create"], endpoint="messages")
        if max_tool_rounds < 0:
            raise ValueError("max_tool_rounds must be greater than or equal to 0")
        self.client = client
        self.model = model
        self.name = name
        self.request_options = _safe_request_options(request_options)
        self.track_cost = track_cost
        self.expand_content_blocks = expand_content_blocks
        self.tools = dict(tools) if tools is not None else None
        self.max_tool_rounds = max_tool_rounds

    def run(
        self,
        task: str,
        input: Any = None,
        *,
        labels: dict[str, str] | None = None,
        output_dir: str = "traces",
        request_options: dict[str, Any] | None = None,
        expand_content_blocks: bool | None = None,
    ) -> AdapterRunResult:
        run_labels = _labels(labels)
        run_labels["adapter"] = self.name
        run_labels["adapter_type"] = "anthropic"
        run_labels["anthropic_endpoint"] = "messages"

        effective_expand_content_blocks = self.expand_content_blocks if expand_content_blocks is None else expand_content_blocks
        if effective_expand_content_blocks:
            run_labels["anthropic_expand_content_blocks"] = "true"
        tool_loop_enabled = self.tools is not None
        if tool_loop_enabled:
            run_labels["anthropic_tool_loop"] = "true"

        options = {"max_tokens": 16000}
        options.update(self.request_options)
        if request_options:
            options.update(request_options)

        messages = _anthropic_messages(input)

        with TraceContext(task=task, labels=run_labels, output_dir=output_dir) as ctx:
            output = None
            error = None
            try:
                for tool_round in range(self.max_tool_rounds + 1):
                    response = None
                    with ctx.step(
                        "model_call",
                        f"{self.name}.messages.create",
                        input={"messages": deepcopy(messages)},
                        model=self.model,
                        replayable=False,
                    ) as step:
                        step.metadata["adapter"] = self.name
                        step.metadata["adapter_type"] = "anthropic"
                        step.metadata["anthropic_endpoint"] = "messages"
                        if tool_loop_enabled:
                            step.metadata["anthropic_tool_round"] = tool_round
                        response = self._create(messages=messages, options=options)
                        output = _anthropic_output(response)
                        message_id = _value(response, "id")
                        request_id = _value(response, "_request_id")
                        stop_reason = _value(response, "stop_reason")
                        if message_id:
                            step.metadata["anthropic_message_id"] = message_id
                        if request_id:
                            step.metadata["anthropic_request_id"] = request_id
                        if stop_reason:
                            step.metadata["anthropic_stop_reason"] = stop_reason
                        cost = _anthropic_cost(response, model=self.model) if self.track_cost else None
                        step.complete(status="success", output=output, cost=cost)
                        if effective_expand_content_blocks:
                            _record_anthropic_content_blocks(ctx, adapter_name=self.name, response=response)

                    tool_uses = _anthropic_tool_use_blocks(response)
                    if not tool_loop_enabled or not tool_uses:
                        break
                    if tool_round >= self.max_tool_rounds:
                        raise RuntimeError(f"Anthropic tool loop exceeded max_tool_rounds={self.max_tool_rounds}")

                    messages.append(
                        {
                            "role": "assistant",
                            "content": _anthropic_message_content(response),
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                self._execute_tool_use(
                                    ctx=ctx,
                                    block=tool_use,
                                    parent_id=step.id,
                                    tool_round=tool_round,
                                    tool_index=tool_index,
                                )
                                for tool_index, tool_use in enumerate(tool_uses)
                            ],
                        }
                    )
            except Exception as exc:
                output = None
                error = Error.from_exc(exc)
                ctx.trace.run.complete(status=_status_for_exception(exc), final_output=error.message)
            else:
                ctx.trace.run.complete(status="success", final_output=output)

            return AdapterRunResult(output=output, trace=ctx.trace, error=error)

    def _create(self, messages: list[dict[str, Any]], options: dict[str, Any]) -> Any:
        create = _create_method(self.client, ["messages", "create"], endpoint="messages")
        return create(model=self.model, messages=deepcopy(messages), **options)

    def _execute_tool_use(
        self,
        *,
        ctx: TraceContext,
        block: Any,
        parent_id: str,
        tool_round: int,
        tool_index: int,
    ) -> dict[str, Any]:
        tool_name = _anthropic_block_tool_name(block, "tool_use")
        tool_use_id = str(_value(block, "id") or "")
        args = _to_serializable(_value(block, "input"))
        tools = self.tools or {}

        with ctx.step("tool_call", f"{self.name}.{tool_name}", input=args, replayable=False, parent_id=parent_id) as step:
            step.metadata["adapter"] = self.name
            step.metadata["adapter_type"] = "anthropic"
            step.metadata["anthropic_tool_loop"] = True
            step.metadata["anthropic_tool_round"] = tool_round
            step.metadata["anthropic_tool_index"] = tool_index
            step.metadata["anthropic_tool_use_id"] = tool_use_id
            step.tool = ToolCall(name=tool_name, args=args)

            fn = tools.get(tool_name)
            if not isinstance(args, dict):
                err = Error(message=f"Anthropic tool input for {tool_name} must be an object", type="InvalidAnthropicToolInput")
                step.complete(status="error", output=err.message, error=err)
                step.tool.result = err.message
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": err.message,
                    "is_error": True,
                }

            if fn is None:
                err = Error(message=f"Unknown Anthropic tool: {tool_name}", type="UnknownAnthropicTool")
                step.complete(status="error", output=err.message, error=err)
                step.tool.result = err.message
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": err.message,
                    "is_error": True,
                }

            try:
                result = _call_anthropic_tool(fn, args)
            except Exception as exc:
                err = Error.from_exc(exc)
                step.complete(status=_status_for_exception(exc), output=err.message, error=err)
                step.tool.result = err.message
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": err.message,
                    "is_error": True,
                }

            step.complete(status="success", output=result)
            step.tool.result = result
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": _anthropic_tool_result_content(result),
            }


def _callable_name(fn: Callable[[Any], Any]) -> str:
    name = getattr(fn, "__name__", "")
    if name and name != "<lambda>":
        return name
    return fn.__class__.__name__


def _labels(labels: dict[str, str] | None) -> dict[str, str]:
    if not labels:
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _safe_request_options(options: dict[str, Any] | None) -> dict[str, Any]:
    if not options:
        return {}
    forbidden = sorted(key for key in options if key.lower() in _FORBIDDEN_REQUEST_OPTION_KEYS)
    if forbidden:
        joined = ", ".join(forbidden)
        raise ValueError(f"request_options cannot override transport/client settings: {joined}")
    return dict(options)


def _status_for_exception(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "error"


def _langgraph_stream_events(chunk: Any, stream_mode: str, stream_index: int) -> list[dict[str, Any]]:
    if isinstance(chunk, dict) and ("data" in chunk or "ns" in chunk or "type" in chunk):
        chunk_type = str(chunk.get("type") or stream_mode)
        namespace = _namespace(chunk.get("ns"))
        return _stream_events_from_data(
            data=chunk.get("data"),
            chunk_type=chunk_type,
            namespace=namespace,
            stream_index=stream_index,
        )

    if isinstance(chunk, dict):
        return _stream_events_from_data(
            data=chunk,
            chunk_type=stream_mode,
            namespace="",
            stream_index=stream_index,
        )

    node = f"stream_{stream_index}"
    return [
        {
            "node": node,
            "namespace": node,
            "chunk_type": stream_mode,
            "output": deepcopy(chunk),
        }
    ]


def _stream_events_from_data(
    data: Any,
    chunk_type: str,
    namespace: str,
    stream_index: int,
) -> list[dict[str, Any]]:
    if isinstance(data, dict) and data:
        events = []
        for node, output in data.items():
            node_name = str(node)
            events.append(
                {
                    "node": node_name,
                    "namespace": namespace or node_name,
                    "chunk_type": chunk_type,
                    "output": deepcopy(output),
                }
            )
        return events

    node = namespace.split("/")[-1] if namespace else f"stream_{stream_index}"
    return [
        {
            "node": node,
            "namespace": namespace or node,
            "chunk_type": chunk_type,
            "output": deepcopy(data),
        }
    ]


def _namespace(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "/".join(str(part) for part in value)
    if value is None:
        return ""
    return str(value)


def _create_method(root: Any, path: list[str], endpoint: str) -> Callable:
    current = root
    for part in path:
        current = getattr(current, part, None)
        if current is None:
            raise TypeError(f"OpenAIAdapter endpoint '{endpoint}' requires client.{'.'.join(path)}")
    if not callable(current):
        raise TypeError(f"OpenAIAdapter endpoint '{endpoint}' requires client.{'.'.join(path)} to be callable")
    return current


def _openai_step_input(endpoint: str, input: Any) -> Any:
    if endpoint == "chat.completions":
        return {"messages": _chat_messages(input)}
    return input


def _chat_messages(input: Any) -> list[dict[str, Any]]:
    if isinstance(input, dict) and isinstance(input.get("messages"), list):
        return deepcopy(input["messages"])
    if isinstance(input, list):
        return deepcopy(input)
    return [{"role": "user", "content": input}]


def _anthropic_messages(input: Any) -> list[dict[str, Any]]:
    if isinstance(input, dict) and isinstance(input.get("messages"), list):
        return deepcopy(input["messages"])
    if isinstance(input, list):
        return deepcopy(input)
    return [{"role": "user", "content": input}]


def _openai_output(response: Any) -> Any:
    output_text = _value(response, "output_text")
    if output_text is not None:
        return output_text

    choices = _value(response, "choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = _value(first, "message")
        if message is not None:
            content = _value(message, "content")
            if content is not None:
                return content
        text = _value(first, "text")
        if text is not None:
            return text

    dumped = _model_dump(response)
    if dumped is not None and dumped is not response:
        return _openai_output(dumped)

    return deepcopy(response) if isinstance(response, (dict, list, str, int, float, bool, type(None))) else str(response)


def _openai_cost(response: Any, model: str) -> Cost | None:
    usage = _value(response, "usage")
    if usage is None:
        dumped = _model_dump(response)
        if isinstance(dumped, dict):
            usage = dumped.get("usage")
    if usage is None:
        return None

    input_tokens = _int_value(usage, "input_tokens") or _int_value(usage, "prompt_tokens")
    output_tokens = _int_value(usage, "output_tokens") or _int_value(usage, "completion_tokens")
    total_tokens = _int_value(usage, "total_tokens") or input_tokens + output_tokens
    amount_usd = _float_value(usage, "amount_usd") or _float_value(usage, "cost")
    if amount_usd == 0.0:
        amount_usd = _compute_usd(model, input_tokens, output_tokens)

    return Cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        amount_usd=amount_usd,
    )


def _record_openai_output_items(ctx: TraceContext, adapter_name: str, response: Any) -> None:
    for index, item in enumerate(_openai_output_items(response)):
        item_type = str(_value(item, "type") or "output_item")
        name = f"{adapter_name}.{_openai_item_name(item, item_type)}"
        step_type = _openai_item_step_type(item_type)
        step_input = _openai_item_input(item, item_type)
        step_output = _openai_item_output(item, item_type)
        status, error = _openai_item_status(item)

        with ctx.step(step_type, name, input=step_input, replayable=False) as step:
            step.metadata["adapter"] = adapter_name
            step.metadata["adapter_type"] = "openai"
            step.metadata["openai_output_item_type"] = item_type
            step.metadata["openai_output_index"] = index
            item_id = _value(item, "id")
            call_id = _value(item, "call_id")
            item_status = _value(item, "status")
            if item_id is not None:
                step.metadata["openai_output_item_id"] = str(item_id)
            if call_id is not None:
                step.metadata["openai_call_id"] = str(call_id)
            if item_status is not None:
                step.metadata["openai_output_item_status"] = str(item_status)
            if step_type == "tool_call":
                step.tool = ToolCall(name=_openai_item_tool_name(item, item_type), args=step_input)
            step.complete(status=status, output=step_output, error=error)
            if step.tool is not None:
                step.tool.result = step.output


def _openai_output_items(response: Any) -> list[Any]:
    output = _value(response, "output")
    if isinstance(output, list):
        return output

    dumped = _model_dump(response)
    if isinstance(dumped, dict) and isinstance(dumped.get("output"), list):
        return dumped["output"]

    return []


def _openai_item_step_type(item_type: str) -> str:
    if item_type in {"file_search_call", "web_search_call"}:
        return "retrieval"
    if item_type == "message":
        return "model_call"
    if item_type == "reasoning":
        return "planner"
    if item_type.endswith("_call") or item_type in {"function_call", "custom_tool_call"}:
        return "tool_call"
    return "custom"


def _openai_item_name(item: Any, item_type: str) -> str:
    if item_type in {"function_call", "custom_tool_call"}:
        return str(_value(item, "name") or item_type)
    return item_type


def _openai_item_tool_name(item: Any, item_type: str) -> str:
    return str(_value(item, "name") or item_type)


def _openai_item_input(item: Any, item_type: str) -> Any:
    if item_type == "file_search_call":
        queries = _value(item, "queries")
        return {"queries": deepcopy(queries)} if queries is not None else _to_serializable(item)
    if item_type in {"function_call", "custom_tool_call"}:
        return _json_or_value(_value(item, "arguments"))
    if item_type.endswith("_call"):
        action = _value(item, "action")
        if action is not None:
            return _json_or_value(action)
        input_value = _value(item, "input")
        return _json_or_value(input_value) if input_value is not None else _to_serializable(item)
    if item_type == "message":
        role = _value(item, "role")
        content = _value(item, "content")
        payload: dict[str, Any] = {}
        if role is not None:
            payload["role"] = role
        if content is not None:
            payload["content"] = _to_serializable(content)
        return payload
    return _to_serializable(item)


def _openai_item_output(item: Any, item_type: str) -> Any:
    if item_type == "file_search_call":
        return _to_serializable(_value(item, "results"))
    if item_type == "message":
        return _message_content_text(_value(item, "content"))
    if item_type == "reasoning":
        return _to_serializable(_value(item, "summary"))
    output = _value(item, "output")
    return _to_serializable(output) if output is not None else None


def _openai_item_status(item: Any) -> tuple[str, Error | None]:
    raw = str(_value(item, "status") or "completed")
    if raw in {"failed", "incomplete", "cancelled"}:
        return "error", Error(message=f"OpenAI output item status: {raw}", type="OpenAIOutputItemStatus")
    return "success", None


def _message_content_text(content: Any) -> Any:
    if not isinstance(content, list):
        return _to_serializable(content)

    texts = []
    for block in content:
        block_type = _value(block, "type")
        text = _value(block, "text")
        if block_type in {"output_text", "text"} and text is not None:
            texts.append(str(text))
    if texts:
        return "\n".join(texts)
    return _to_serializable(content)


def _json_or_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return _to_serializable(value)


def _to_serializable(value: Any) -> Any:
    dumped = _model_dump(value)
    if dumped is not None:
        return dumped
    if isinstance(value, (dict, list, str, int, float, bool, type(None))):
        return deepcopy(value)
    return str(value)


def _anthropic_output(response: Any) -> Any:
    content = _value(response, "content")
    if isinstance(content, list):
        text_blocks = []
        for block in content:
            block_type = _value(block, "type")
            text = _value(block, "text")
            if block_type == "text" and text is not None:
                text_blocks.append(str(text))
        if text_blocks:
            return "\n".join(text_blocks)
        return [_block_to_value(block) for block in content]

    if content is not None:
        return deepcopy(content)

    dumped = _model_dump(response)
    if dumped is not None and dumped is not response:
        return _anthropic_output(dumped)

    return deepcopy(response) if isinstance(response, (dict, list, str, int, float, bool, type(None))) else str(response)


def _anthropic_cost(response: Any, model: str) -> Cost | None:
    usage = _value(response, "usage")
    if usage is None:
        dumped = _model_dump(response)
        if isinstance(dumped, dict):
            usage = dumped.get("usage")
    if usage is None:
        return None

    base_input_tokens = _int_value(usage, "input_tokens")
    cache_creation_tokens = _int_value(usage, "cache_creation_input_tokens")
    cache_read_tokens = _int_value(usage, "cache_read_input_tokens")
    input_tokens = base_input_tokens + cache_creation_tokens + cache_read_tokens
    output_tokens = _int_value(usage, "output_tokens")
    total_tokens = _int_value(usage, "total_tokens") or input_tokens + output_tokens
    amount_usd = _float_value(usage, "amount_usd") or _float_value(usage, "cost")
    if amount_usd == 0.0:
        amount_usd = _compute_usd(model, input_tokens, output_tokens)

    return Cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        amount_usd=amount_usd,
    )


def _record_anthropic_content_blocks(ctx: TraceContext, adapter_name: str, response: Any) -> None:
    for index, block in enumerate(_anthropic_content_blocks(response)):
        block_type = str(_value(block, "type") or "content_block")
        step_type = _anthropic_block_step_type(block_type)
        step_name = f"{adapter_name}.{_anthropic_block_name(block, block_type)}"
        step_input = _anthropic_block_input(block, block_type)
        step_output = _anthropic_block_output(block, block_type)

        with ctx.step(step_type, step_name, input=step_input, replayable=False) as step:
            step.metadata["adapter"] = adapter_name
            step.metadata["adapter_type"] = "anthropic"
            step.metadata["anthropic_content_block_type"] = block_type
            step.metadata["anthropic_content_index"] = index
            block_id = _value(block, "id")
            if block_id is not None:
                step.metadata["anthropic_content_block_id"] = str(block_id)
            if block_type in {"tool_use", "server_tool_use"}:
                step.metadata["anthropic_tool_use_id"] = str(block_id) if block_id is not None else ""
                step.tool = ToolCall(name=_anthropic_block_tool_name(block, block_type), args=step_input)
            step.complete(status="success", output=step_output)
            if step.tool is not None:
                step.tool.result = step.output


def _anthropic_content_blocks(response: Any) -> list[Any]:
    content = _value(response, "content")
    if isinstance(content, list):
        return content

    dumped = _model_dump(response)
    if isinstance(dumped, dict) and isinstance(dumped.get("content"), list):
        return dumped["content"]

    return []


def _anthropic_tool_use_blocks(response: Any) -> list[Any]:
    return [
        block
        for block in _anthropic_content_blocks(response)
        if str(_value(block, "type") or "") in {"tool_use", "server_tool_use"}
    ]


def _anthropic_message_content(response: Any) -> list[Any]:
    return [_anthropic_content_block_message_value(block) for block in _anthropic_content_blocks(response)]


def _anthropic_content_block_message_value(block: Any) -> Any:
    dumped = _model_dump(block)
    if isinstance(dumped, dict):
        return dumped
    if isinstance(block, dict):
        return deepcopy(block)

    keys = [
        "id",
        "input",
        "name",
        "type",
        "text",
        "thinking",
        "data",
        "signature",
        "content",
        "citations",
    ]
    value = {key: _to_serializable(_value(block, key)) for key in keys if _value(block, key) is not None}
    return value if value else _to_serializable(block)


def _anthropic_block_step_type(block_type: str) -> str:
    if block_type in {"tool_use", "server_tool_use"} or block_type.endswith("_tool_use"):
        return "tool_call"
    if block_type in {"thinking", "redacted_thinking"}:
        return "planner"
    if block_type == "text":
        return "model_call"
    if block_type.endswith("_tool_result") or block_type == "tool_result":
        return "tool_call"
    return "custom"


def _anthropic_block_name(block: Any, block_type: str) -> str:
    if block_type in {"tool_use", "server_tool_use"}:
        return _anthropic_block_tool_name(block, block_type)
    return block_type


def _anthropic_block_tool_name(block: Any, block_type: str) -> str:
    return str(_value(block, "name") or block_type)


def _anthropic_block_input(block: Any, block_type: str) -> Any:
    if block_type in {"tool_use", "server_tool_use"}:
        return _to_serializable(_value(block, "input"))
    return _to_serializable(block)


def _anthropic_block_output(block: Any, block_type: str) -> Any:
    if block_type == "text":
        return _value(block, "text")
    if block_type == "thinking":
        return _value(block, "thinking")
    if block_type == "redacted_thinking":
        return _to_serializable(_value(block, "data"))
    if block_type.endswith("_tool_result") or block_type == "tool_result":
        content = _value(block, "content")
        return _to_serializable(content) if content is not None else _to_serializable(block)
    output = _value(block, "output")
    if output is not None:
        return _to_serializable(output)
    return None


def _block_to_value(block: Any) -> Any:
    dumped = _model_dump(block)
    if dumped is not None:
        return dumped
    if isinstance(block, (dict, list, str, int, float, bool, type(None))):
        return deepcopy(block)
    return str(block)


def _call_anthropic_tool(fn: Callable[..., Any], args: Any) -> Any:
    if not isinstance(args, dict):
        raise TypeError("Anthropic tool input must be an object")
    return fn(**args)


def _anthropic_tool_result_content(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(_to_serializable(result), ensure_ascii=False)


def _value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    value = getattr(obj, key, None)
    if value is not None and not callable(value):
        return value
    return None


def _int_value(obj: Any, key: str) -> int:
    value = _value(obj, key)
    if value is None:
        return 0
    return int(value)


def _float_value(obj: Any, key: str) -> float:
    value = _value(obj, key)
    if value is None:
        return 0.0
    return float(value)


def _model_dump(obj: Any) -> Any:
    if isinstance(obj, (dict, list, str, int, float, bool, type(None))):
        return None
    module_name = obj.__class__.__module__
    if not (
        module_name.startswith("openai")
        or module_name.startswith("anthropic")
        or module_name.startswith("pydantic")
    ):
        return None
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump()
    return None
