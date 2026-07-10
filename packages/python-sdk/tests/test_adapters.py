"""Tests for framework adapter contracts."""

from __future__ import annotations

from agent_devtools import AnthropicAdapter, CallableAgentAdapter, LangGraphAdapter, OpenAIAdapter, current_trace


def test_callable_agent_adapter_records_successful_callable_run(tmp_path) -> None:
    def agent(payload: dict[str, str]) -> dict[str, str]:
        assert current_trace() is not None
        return {"answer": payload["question"].upper()}

    adapter = CallableAgentAdapter(agent, name="demo-agent")

    result = adapter.run(
        task="Answer question",
        input={"question": "weather"},
        labels={"scenario": "success"},
        output_dir=str(tmp_path),
    )

    assert result.error is None
    assert result.output == {"answer": "WEATHER"}
    assert result.trace.run.status == "success"
    assert result.trace.run.final_output == {"answer": "WEATHER"}
    assert result.trace.run.labels["adapter"] == "demo-agent"
    assert result.trace.run.labels["scenario"] == "success"
    assert len(result.trace.steps) == 1
    assert result.trace.steps[0].name == "demo-agent.run"
    assert result.trace.steps[0].input == {"question": "weather"}
    assert result.trace.steps[0].output == {"answer": "WEATHER"}
    assert (tmp_path / f"{result.trace.run.id}.trace.json").exists()


def test_callable_agent_adapter_captures_errors_as_failed_traces(tmp_path) -> None:
    def agent(payload: dict[str, str]) -> str:
        raise RuntimeError(f"cannot answer {payload['question']}")

    adapter = CallableAgentAdapter(agent, name="broken-agent")

    result = adapter.run(
        task="Failing question",
        input={"question": "weather"},
        output_dir=str(tmp_path),
    )

    assert result.output is None
    assert result.error is not None
    assert result.error.type == "RuntimeError"
    assert "cannot answer weather" in result.error.message
    assert result.trace.run.status == "error"
    assert "cannot answer weather" in result.trace.run.final_output
    assert result.trace.run.labels["adapter"] == "broken-agent"
    assert len(result.trace.steps) == 1
    assert result.trace.steps[0].status == "error"
    assert result.trace.steps[0].error is not None
    assert result.trace.steps[0].error.type == "RuntimeError"
    assert (tmp_path / f"{result.trace.run.id}.trace.json").exists()


def test_langgraph_adapter_invokes_graph_and_records_trace(tmp_path) -> None:
    class FakeGraph:
        def __init__(self) -> None:
            self.calls = []

        def invoke(self, state, config=None):
            self.calls.append((state, config))
            return {"messages": ["ok"], "question": state["question"]}

    graph = FakeGraph()
    adapter = LangGraphAdapter(graph, name="qa-graph", config={"configurable": {"thread_id": "t-1"}})

    result = adapter.run(
        task="Run LangGraph",
        input={"question": "weather"},
        labels={"scenario": "langgraph"},
        output_dir=str(tmp_path),
    )

    assert graph.calls == [
        (
            {"question": "weather"},
            {"configurable": {"thread_id": "t-1"}},
        )
    ]
    assert result.error is None
    assert result.output == {"messages": ["ok"], "question": "weather"}
    assert result.trace.run.status == "success"
    assert result.trace.run.labels["adapter"] == "qa-graph"
    assert result.trace.run.labels["adapter_type"] == "langgraph"
    assert result.trace.run.labels["scenario"] == "langgraph"
    assert len(result.trace.steps) == 1
    assert result.trace.steps[0].name == "qa-graph.invoke"
    assert result.trace.steps[0].input == {"question": "weather"}
    assert result.trace.steps[0].output == {"messages": ["ok"], "question": "weather"}
    assert result.trace.steps[0].metadata["adapter_type"] == "langgraph"
    assert (tmp_path / f"{result.trace.run.id}.trace.json").exists()


def test_langgraph_adapter_streams_node_updates_as_steps(tmp_path) -> None:
    class FakeStreamingGraph:
        def __init__(self) -> None:
            self.stream_calls = []

        def invoke(self, state, config=None):
            raise AssertionError("streaming adapter should not call invoke")

        def stream(self, state, config=None, stream_mode=None, version=None):
            self.stream_calls.append((state, config, stream_mode, version))
            yield {
                "type": "updates",
                "ns": ["planner"],
                "data": {"planner": {"plan": "call weather"}},
            }
            yield {
                "type": "updates",
                "ns": ["weather"],
                "data": {"weather": {"summary": "warm"}},
            }

    graph = FakeStreamingGraph()
    adapter = LangGraphAdapter(
        graph,
        name="qa-graph",
        config={"configurable": {"thread_id": "t-1"}},
        trace_stream=True,
    )

    result = adapter.run(
        task="Stream LangGraph",
        input={"question": "weather"},
        output_dir=str(tmp_path),
    )

    assert graph.stream_calls == [
        (
            {"question": "weather"},
            {"configurable": {"thread_id": "t-1"}},
            "updates",
            "v2",
        )
    ]
    assert result.error is None
    assert result.output == {
        "planner": {"plan": "call weather"},
        "weather": {"summary": "warm"},
    }
    assert result.trace.run.status == "success"
    assert result.trace.run.labels["adapter"] == "qa-graph"
    assert result.trace.run.labels["adapter_type"] == "langgraph"
    assert result.trace.run.labels["langgraph_stream"] == "true"
    assert [step.name for step in result.trace.steps] == ["qa-graph.planner", "qa-graph.weather"]
    assert result.trace.steps[0].output == {"plan": "call weather"}
    assert result.trace.steps[0].metadata["langgraph_node"] == "planner"
    assert result.trace.steps[0].metadata["langgraph_namespace"] == "planner"
    assert result.trace.steps[0].metadata["langgraph_stream_index"] == 0
    assert result.trace.steps[1].output == {"summary": "warm"}
    assert result.trace.steps[1].metadata["langgraph_node"] == "weather"
    assert result.trace.steps[1].metadata["langgraph_namespace"] == "weather"
    assert result.trace.steps[1].metadata["langgraph_stream_index"] == 1


def test_langgraph_adapter_streaming_requires_stream_method() -> None:
    class InvokeOnlyGraph:
        def invoke(self, state, config=None):
            return state

    try:
        LangGraphAdapter(InvokeOnlyGraph(), trace_stream=True)
    except TypeError as exc:
        assert "stream" in str(exc)
    else:
        raise AssertionError("expected TypeError")


def test_langgraph_adapter_captures_invoke_errors_as_failed_traces(tmp_path) -> None:
    class FailingGraph:
        def invoke(self, state, config=None):
            raise RuntimeError(f"graph failed for {state['question']}")

    adapter = LangGraphAdapter(FailingGraph(), name="broken-graph")

    result = adapter.run(
        task="Run broken LangGraph",
        input={"question": "weather"},
        output_dir=str(tmp_path),
    )

    assert result.output is None
    assert result.error is not None
    assert result.error.type == "RuntimeError"
    assert "graph failed for weather" in result.error.message
    assert result.trace.run.status == "error"
    assert result.trace.run.labels["adapter"] == "broken-graph"
    assert result.trace.run.labels["adapter_type"] == "langgraph"
    assert result.trace.steps[0].status == "error"
    assert result.trace.steps[0].error is not None
    assert result.trace.steps[0].error.type == "RuntimeError"


def test_langgraph_adapter_requires_invoke_method() -> None:
    try:
        LangGraphAdapter(object())
    except TypeError as exc:
        assert "invoke" in str(exc)
    else:
        raise AssertionError("expected TypeError")


def test_openai_adapter_records_responses_create_and_cost(tmp_path) -> None:
    class Usage:
        input_tokens = 12
        output_tokens = 8
        total_tokens = 20

    class Response:
        id = "resp_123"
        output_text = "The weather is warm."
        usage = Usage()

    class Responses:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return Response()

    class Client:
        def __init__(self) -> None:
            self.responses = Responses()

    client = Client()
    adapter = OpenAIAdapter(
        client,
        model="gpt-4.1-mini",
        name="openai-weather",
        request_options={"temperature": 0},
    )

    result = adapter.run(
        task="Ask OpenAI",
        input="weather in Shanghai",
        labels={"scenario": "responses"},
        output_dir=str(tmp_path),
    )

    assert client.responses.calls == [
        {
            "model": "gpt-4.1-mini",
            "input": "weather in Shanghai",
            "temperature": 0,
        }
    ]
    assert result.error is None
    assert result.output == "The weather is warm."
    assert result.trace.run.status == "success"
    assert result.trace.run.final_output == "The weather is warm."
    assert result.trace.run.labels["adapter"] == "openai-weather"
    assert result.trace.run.labels["adapter_type"] == "openai"
    assert result.trace.run.labels["openai_endpoint"] == "responses"
    assert result.trace.run.labels["scenario"] == "responses"
    assert len(result.trace.steps) == 1
    step = result.trace.steps[0]
    assert step.type == "model_call"
    assert step.name == "openai-weather.responses.create"
    assert step.model == "gpt-4.1-mini"
    assert step.input == "weather in Shanghai"
    assert step.output == "The weather is warm."
    assert step.metadata["openai_response_id"] == "resp_123"
    assert step.cost is not None
    assert step.cost.input_tokens == 12
    assert step.cost.output_tokens == 8
    assert step.cost.total_tokens == 20
    assert step.cost.amount_usd > 0
    assert (tmp_path / f"{result.trace.run.id}.trace.json").exists()


def test_openai_adapter_can_expand_responses_output_items_as_child_steps(tmp_path) -> None:
    class Usage:
        input_tokens = 20
        output_tokens = 10
        total_tokens = 30

    class Response:
        id = "resp_items"
        output_text = "Final answer."
        usage = Usage()
        output = [
            {
                "type": "file_search_call",
                "id": "fs_1",
                "status": "completed",
                "queries": ["weather report"],
                "results": [{"filename": "weather.txt", "score": 0.9}],
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "get_weather",
                "arguments": '{"city":"Shanghai"}',
                "status": "completed",
            },
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Final answer.",
                        "annotations": [],
                    }
                ],
            },
        ]

    class Responses:
        def create(self, **kwargs):
            return Response()

    class Client:
        responses = Responses()

    adapter = OpenAIAdapter(
        Client(),
        model="gpt-4.1-mini",
        name="openai-weather",
        expand_output_items=True,
    )

    result = adapter.run(task="Ask OpenAI with tools", input="weather", output_dir=str(tmp_path))

    assert result.error is None
    assert [step.name for step in result.trace.steps] == [
        "openai-weather.responses.create",
        "openai-weather.file_search_call",
        "openai-weather.get_weather",
        "openai-weather.message",
    ]
    parent_id = result.trace.steps[0].id
    file_search, function_call, message = result.trace.steps[1:]
    assert file_search.parent_id == parent_id
    assert file_search.type == "retrieval"
    assert file_search.input == {"queries": ["weather report"]}
    assert file_search.output == [{"filename": "weather.txt", "score": 0.9}]
    assert file_search.metadata["openai_output_item_type"] == "file_search_call"
    assert function_call.parent_id == parent_id
    assert function_call.type == "tool_call"
    assert function_call.tool is not None
    assert function_call.tool.name == "get_weather"
    assert function_call.tool.args == {"city": "Shanghai"}
    assert function_call.metadata["openai_call_id"] == "call_1"
    assert message.parent_id == parent_id
    assert message.type == "model_call"
    assert message.output == "Final answer."
    assert message.metadata["openai_output_item_id"] == "msg_1"


def test_openai_adapter_records_chat_completions_create_and_cost(tmp_path) -> None:
    class Usage:
        prompt_tokens = 9
        completion_tokens = 4
        total_tokens = 13

    class Message:
        content = "Warm and clear."

    class Choice:
        message = Message()

    class Response:
        id = "chatcmpl_123"
        choices = [Choice()]
        usage = Usage()

    class Completions:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return Response()

    class Chat:
        def __init__(self) -> None:
            self.completions = Completions()

    class Client:
        def __init__(self) -> None:
            self.chat = Chat()

    client = Client()
    adapter = OpenAIAdapter(client, model="gpt-4o-mini", endpoint="chat.completions", name="openai-chat")

    result = adapter.run(
        task="Ask Chat Completions",
        input="weather in Shanghai",
        output_dir=str(tmp_path),
    )

    assert client.chat.completions.calls == [
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "weather in Shanghai"}],
        }
    ]
    assert result.error is None
    assert result.output == "Warm and clear."
    assert result.trace.run.labels["openai_endpoint"] == "chat.completions"
    step = result.trace.steps[0]
    assert step.name == "openai-chat.chat.completions.create"
    assert step.input == {"messages": [{"role": "user", "content": "weather in Shanghai"}]}
    assert step.output == "Warm and clear."
    assert step.metadata["openai_response_id"] == "chatcmpl_123"
    assert step.cost is not None
    assert step.cost.input_tokens == 9
    assert step.cost.output_tokens == 4
    assert step.cost.total_tokens == 13


def test_openai_adapter_captures_api_errors_as_failed_traces(tmp_path) -> None:
    class Responses:
        def create(self, **kwargs):
            raise RuntimeError("rate limit")

    class Client:
        responses = Responses()

    adapter = OpenAIAdapter(Client(), model="gpt-4.1-mini")

    result = adapter.run(task="Failing OpenAI call", input="hello", output_dir=str(tmp_path))

    assert result.output is None
    assert result.error is not None
    assert result.error.type == "RuntimeError"
    assert "rate limit" in result.error.message
    assert result.trace.run.status == "error"
    assert result.trace.run.labels["adapter_type"] == "openai"
    assert result.trace.steps[0].status == "error"
    assert result.trace.steps[0].error is not None
    assert result.trace.steps[0].error.type == "RuntimeError"


def test_openai_adapter_rejects_unknown_endpoint() -> None:
    try:
        OpenAIAdapter(object(), model="gpt-4.1-mini", endpoint="unknown")
    except ValueError as exc:
        assert "endpoint" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_anthropic_adapter_records_messages_create_and_cost(tmp_path) -> None:
    class Usage:
        input_tokens = 14
        output_tokens = 6

    class TextBlock:
        type = "text"
        text = "The weather is warm."

    class Response:
        id = "msg_123"
        model = "claude-opus-4-8"
        content = [TextBlock()]
        usage = Usage()
        stop_reason = "end_turn"
        _request_id = "req_123"

    class Messages:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return Response()

    class Client:
        def __init__(self) -> None:
            self.messages = Messages()

    client = Client()
    adapter = AnthropicAdapter(
        client,
        model="claude-opus-4-8",
        name="claude-weather",
        request_options={"max_tokens": 1024, "system": "Answer briefly."},
    )

    result = adapter.run(
        task="Ask Claude",
        input="weather in Shanghai",
        labels={"scenario": "messages"},
        output_dir=str(tmp_path),
    )

    assert client.messages.calls == [
        {
            "model": "claude-opus-4-8",
            "max_tokens": 1024,
            "system": "Answer briefly.",
            "messages": [{"role": "user", "content": "weather in Shanghai"}],
        }
    ]
    assert result.error is None
    assert result.output == "The weather is warm."
    assert result.trace.run.status == "success"
    assert result.trace.run.final_output == "The weather is warm."
    assert result.trace.run.labels["adapter"] == "claude-weather"
    assert result.trace.run.labels["adapter_type"] == "anthropic"
    assert result.trace.run.labels["anthropic_endpoint"] == "messages"
    assert result.trace.run.labels["scenario"] == "messages"
    assert len(result.trace.steps) == 1
    step = result.trace.steps[0]
    assert step.type == "model_call"
    assert step.name == "claude-weather.messages.create"
    assert step.model == "claude-opus-4-8"
    assert step.input == {"messages": [{"role": "user", "content": "weather in Shanghai"}]}
    assert step.output == "The weather is warm."
    assert step.metadata["anthropic_message_id"] == "msg_123"
    assert step.metadata["anthropic_request_id"] == "req_123"
    assert step.metadata["anthropic_stop_reason"] == "end_turn"
    assert step.cost is not None
    assert step.cost.input_tokens == 14
    assert step.cost.output_tokens == 6
    assert step.cost.total_tokens == 20
    assert step.cost.amount_usd > 0
    assert (tmp_path / f"{result.trace.run.id}.trace.json").exists()


def test_anthropic_adapter_can_expand_content_blocks_as_child_steps(tmp_path) -> None:
    class Usage:
        input_tokens = 18
        output_tokens = 9

    class ThinkingBlock:
        id = "thinking_1"
        type = "thinking"
        thinking = "Need current weather."

    class ToolUseBlock:
        id = "toolu_1"
        type = "tool_use"
        name = "get_weather"
        input = {"city": "Shanghai"}

    class TextBlock:
        id = "text_1"
        type = "text"
        text = "I will check the weather."

    class Response:
        id = "msg_blocks"
        content = [ThinkingBlock(), ToolUseBlock(), TextBlock()]
        usage = Usage()
        stop_reason = "tool_use"
        _request_id = "req_blocks"

    class Messages:
        def create(self, **kwargs):
            return Response()

    class Client:
        messages = Messages()

    adapter = AnthropicAdapter(
        Client(),
        model="claude-opus-4-8",
        name="claude-weather",
        expand_content_blocks=True,
    )

    result = adapter.run(task="Ask Claude with tools", input="weather", output_dir=str(tmp_path))

    assert result.error is None
    assert [step.name for step in result.trace.steps] == [
        "claude-weather.messages.create",
        "claude-weather.thinking",
        "claude-weather.get_weather",
        "claude-weather.text",
    ]
    parent_id = result.trace.steps[0].id
    thinking, tool_use, text = result.trace.steps[1:]
    assert thinking.parent_id == parent_id
    assert thinking.type == "planner"
    assert thinking.output == "Need current weather."
    assert thinking.metadata["anthropic_content_block_type"] == "thinking"
    assert tool_use.parent_id == parent_id
    assert tool_use.type == "tool_call"
    assert tool_use.tool is not None
    assert tool_use.tool.name == "get_weather"
    assert tool_use.tool.args == {"city": "Shanghai"}
    assert tool_use.metadata["anthropic_tool_use_id"] == "toolu_1"
    assert text.parent_id == parent_id
    assert text.type == "model_call"
    assert text.output == "I will check the weather."
    assert text.metadata["anthropic_content_block_id"] == "text_1"
    assert result.trace.run.labels["anthropic_expand_content_blocks"] == "true"


def test_anthropic_adapter_accepts_existing_messages_input(tmp_path) -> None:
    class Usage:
        input_tokens = 5
        output_tokens = 2

    class TextBlock:
        type = "text"
        text = "OK"

    class Response:
        content = [TextBlock()]
        usage = Usage()

    class Messages:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return Response()

    class Client:
        def __init__(self) -> None:
            self.messages = Messages()

    client = Client()
    adapter = AnthropicAdapter(client, model="claude-haiku-4-5")
    messages = [{"role": "user", "content": "hello"}]

    result = adapter.run(task="Existing messages", input={"messages": messages}, output_dir=str(tmp_path))

    assert client.messages.calls == [
        {
            "model": "claude-haiku-4-5",
            "max_tokens": 16000,
            "messages": messages,
        }
    ]
    assert result.output == "OK"
    assert result.trace.steps[0].input == {"messages": messages}
    assert result.trace.steps[0].cost is not None
    assert result.trace.steps[0].cost.total_tokens == 7


def test_anthropic_adapter_captures_api_errors_as_failed_traces(tmp_path) -> None:
    class Messages:
        def create(self, **kwargs):
            raise RuntimeError("overloaded")

    class Client:
        messages = Messages()

    adapter = AnthropicAdapter(Client(), model="claude-opus-4-8")

    result = adapter.run(task="Failing Claude call", input="hello", output_dir=str(tmp_path))

    assert result.output is None
    assert result.error is not None
    assert result.error.type == "RuntimeError"
    assert "overloaded" in result.error.message
    assert result.trace.run.status == "error"
    assert result.trace.run.labels["adapter_type"] == "anthropic"
    assert result.trace.steps[0].status == "error"
    assert result.trace.steps[0].error is not None
    assert result.trace.steps[0].error.type == "RuntimeError"


def test_anthropic_adapter_requires_messages_create() -> None:
    try:
        AnthropicAdapter(object(), model="claude-opus-4-8")
    except TypeError as exc:
        assert "messages.create" in str(exc)
    else:
        raise AssertionError("expected TypeError")
