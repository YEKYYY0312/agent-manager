# Agent DevTools 中文快速使用指南

这份指南给日常使用看，不讲太多实现细节。

## 这个工具是干什么的

Agent DevTools 用来观察一次 AI Agent 任务到底发生了什么：

- 每一步做了什么
- 哪一步失败了
- 哪一步最慢
- 哪个模型调用花了 token 和费用
- 两次运行有什么不同

你可以把它理解成 Agent 的本地调试器。

## 1. 生成一批示例 Trace

在项目根目录运行：

```powershell
py examples\simple-agent\demo.py
```

它会在 `traces/` 目录生成 3 类文件：

- 成功运行：有 planner、tool、model 三步
- 失败运行：tool 超时，run 变成 error
- decorator-only 运行：全部步骤通过装饰器记录

Trace 文件名长这样：

```text
traces\d6c78b2ba059.trace.json
```

每次运行生成的 id 都不一样，所以文件名不是固定的。

## 2. 用 CLI 查看 Trace

列出所有 trace：

```powershell
py packages\cli\agent_devtools_cli\main.py list traces
```

查看一次运行的摘要和时间线：

```powershell
py packages\cli\agent_devtools_cli\main.py show traces\<trace-file>.trace.json --detail
```

查看每一步：

```powershell
py packages\cli\agent_devtools_cli\main.py steps traces\<trace-file>.trace.json
```

看 token 和费用：

```powershell
py packages\cli\agent_devtools_cli\main.py cost traces\<trace-file>.trace.json
```

做完整分析：

```powershell
py packages\cli\agent_devtools_cli\main.py analyze traces\<trace-file>.trace.json
```

从某一步开始生成回放 trace：

```powershell
py packages\cli\agent_devtools_cli\main.py replay traces\<trace-file>.trace.json --start-step <step-id>
```

CLI 的 `replay` 是确定性回放：它复用已经记录下来的 tool/model 输出。

如果你在 Web UI 里编辑了工具 Mock，可以用 Replay Plan JSON 执行：

```powershell
py packages\cli\agent_devtools_cli\main.py replay traces\<trace-file>.trace.json --plan replay-plan.json --output-dir traces
```

如果你要让真实本地 Python callable 重新执行，用 `replay-adapter`，并显式传入 `--callable`：

```powershell
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\<trace-file>.trace.json --start-step <step-id> --callable path\to\agent.py:run --allow-unsafe-code --output-dir traces
```

`--callable` 支持 `module:function` 或 `path\to\file.py:function`。这个命令会执行你的本地 Python 代码，所以默认会拒绝运行；只有你确认代码可信时才加 `--allow-unsafe-code`。callable 会在子 Python 进程里运行，临时 import path 不会污染主 CLI 进程，但这不是沙箱。

生成 replay trace 后，可以自动对比原始运行和 replay 运行：

```powershell
py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json
```

它会只比较原始 trace 里从 `source_start_step_id` 开始的那段路径，报告状态、输出、步骤数、耗时、Token 和费用有没有变化。

也可以直接用 SDK 的 adapter API：

```python
from agent_devtools import AnthropicAdapter, CallableAgentAdapter, LangGraphAdapter, OpenAIAdapter, Trace, replay_with_adapter

source = Trace.from_file("traces/source.trace.json")
adapter = CallableAgentAdapter(lambda payload: {"answer": payload}, name="demo-agent")
result = replay_with_adapter(source, start_step_id="<step-id>", adapter=adapter)

# 如果你已经有编译好的 LangGraph graph：
langgraph_adapter = LangGraphAdapter(compiled_graph, name="qa-graph")
langgraph_result = langgraph_adapter.run(task="Run graph", input={"messages": messages})

# 如果想把 LangGraph 每个 node update 展开成单独 step：
streaming_adapter = LangGraphAdapter(compiled_graph, name="qa-graph", trace_stream=True)
streaming_result = streaming_adapter.run(task="Run graph", input={"messages": messages})

# 如果你有 OpenAI Python SDK client：
openai_adapter = OpenAIAdapter(openai_client, model="gpt-4.1-mini")
openai_result = openai_adapter.run(task="Ask model", input="weather in Shanghai")

# 如果想把 OpenAI Responses output item 展开成子 step：
openai_expanded = OpenAIAdapter(openai_client, model="gpt-4.1-mini", expand_output_items=True)
openai_expanded_result = openai_expanded.run(task="Ask model", input="weather in Shanghai")

# 如果你有 Anthropic Python SDK client：
anthropic_adapter = AnthropicAdapter(anthropic_client, model="claude-opus-4-8")
anthropic_result = anthropic_adapter.run(task="Ask Claude", input="weather in Shanghai")

# 如果想把 Claude Messages content block 展开成子 step：
anthropic_expanded = AnthropicAdapter(anthropic_client, model="claude-opus-4-8", expand_content_blocks=True)
anthropic_expanded_result = anthropic_expanded.run(task="Ask Claude", input="weather in Shanghai")

# 如果想让 Claude tool_use 调用本地 Python 工具并继续生成最终答案：
def get_weather(city: str) -> dict[str, str]:
    return {"summary": f"{city}: cool and windy"}

anthropic_tools = AnthropicAdapter(
    anthropic_client,
    model="claude-opus-4-8",
    request_options={
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather for a city.",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ]
    },
    tools={"get_weather": get_weather},
)
anthropic_tools_result = anthropic_tools.run(task="Ask Claude with tools", input="weather in Shanghai")
```

对比两次运行：

```powershell
py packages\cli\agent_devtools_cli\main.py diff traces\<left>.trace.json traces\<right>.trace.json
```

把两次运行当作 A/B 实验比较：

```powershell
py packages\cli\agent_devtools_cli\main.py experiment traces\<left>.trace.json traces\<right>.trace.json
```

把两次运行当作 CI 回归门禁：

```powershell
py packages\cli\agent_devtools_cli\main.py regression-check traces\<baseline>.trace.json traces\<candidate>.trace.json --max-token-delta 100 --max-latency-delta-ms 500 --json
```

这个命令适合放进 CI：全部通过返回 `0`，发现状态变差、失败步骤增加，或 Token/费用/耗时/步骤数超过阈值时返回 `1`。如果最终输出变化也应该算失败，加 `--fail-on-output-change`。

分享前生成脱敏副本：

```powershell
py packages\cli\agent_devtools_cli\main.py privacy-scan traces\<trace-file>.trace.json
py packages\cli\agent_devtools_cli\main.py redact traces\<trace-file>.trace.json --output traces\<trace-file>.safe.trace.json
```

导入本地 SQLite 库并搜索：

```powershell
py packages\cli\agent_devtools_cli\main.py store import traces --redact
py packages\cli\agent_devtools_cli\main.py store list
py packages\cli\agent_devtools_cli\main.py store search "weather"
```

导出成 OpenTelemetry 兼容的 OTLP JSON：

```powershell
py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace-file>.trace.json --redact --output traces\<trace-file>.otlp.json
```

直接推送到本地 OpenTelemetry Collector：

```powershell
py packages\cli\agent_devtools_cli\main.py otel-push traces\<trace-file>.trace.json --redact --endpoint http://localhost:4318/v1/traces
```

如果不传 `--endpoint`，会依次读取 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`、`OTEL_EXPORTER_OTLP_ENDPOINT`，最后默认用 `http://localhost:4318/v1/traces`。

`otel-push` 默认只允许 HTTPS 或本机 loopback HTTP。非本机 HTTP 需要 `--allow-insecure-endpoint`，内网/link-local 等私有地址需要 `--allow-private-endpoint`，只对可信 Collector 使用这些开关。

`privacy-scan` 只报告敏感内容的位置和类型，不会把密钥原文打印出来。`store import`、`otel-export` 和 `otel-push` 默认会先做隐私预检；发现敏感内容时会停止。建议用 `--redact` 写入或推送脱敏版本。只有你明确知道风险并且需要原始 trace 时，才使用 `--allow-sensitive`。

默认不会导出 step input/output 和 tool args/result，避免把敏感内容带出去。只有你明确需要这些 payload 时，再加 `--include-payloads`。

如果你想让 SDK 写 trace 或 SQLite 时自动脱敏，可以在当前终端设置：

```powershell
$env:AGENT_DEVTOOLS_REDACT_ON_WRITE = "true"
```

## 3. 打开 Web UI

进入 Web UI 目录：

```powershell
cd packages\web-ui
npm run dev
```

然后打开终端里显示的地址，通常是：

```text
http://127.0.0.1:5173/
```

如果浏览器显示拒绝连接，通常是 dev server 没启动，重新运行 `npm run dev` 即可。

## 4. Web UI 怎么看

左侧是 Trace 列表：选择一次运行记录。

上方摘要栏：看运行状态、耗时、步骤数、token 和费用。

时间线 Timeline：看 Agent 每一步的执行顺序。

步骤详情 Inspector：点时间线中的某一步，右侧查看输入、输出、工具参数、错误信息。

分析 Analysis：看总费用、最慢步骤、失败/超时步骤。

运行对比 Run Diff：选择两个 trace，对比步骤数、耗时、token、费用和步骤差异。

回放 Replay：选择一个可回放步骤，查看从该步骤开始的回放范围，编辑工具 Mock result JSON，复制 Replay CLI 命令和 Replay Plan JSON。

回放对比 Replay Compare：导入 replay trace 后，选择它来查看来源是否匹配、状态/输出/步骤/Token/费用/耗时有没有变化。

实验对比 Experiment：选择一个 B Trace，把当前 Trace 当作 A，查看成功状态、费用、延迟、输出变化和推荐结果。

## 5. 导入自己的 Trace

在 Web UI 左侧 Trace 列表点击“导入”，选择本地 `.trace.json` 文件。

也可以把 `.trace.json` 文件拖到左侧 Trace 区域。

导入后它会加入当前列表，并自动选中。导入记录会保存到当前浏览器的本地存储，刷新页面后仍会恢复；换浏览器或清理站点数据后需要重新导入。

## 6. 看问题时的顺序

建议按这个顺序看：

1. Run 状态是不是成功。
2. Timeline 里第一条失败步骤是哪一步。
3. Inspector 里错误信息是什么。
4. Analysis 里最慢步骤是哪一步。
5. Cost 里是不是某个模型调用 token 特别高。
6. Run Diff 里成功和失败运行从哪一步开始分叉。
7. Experiment 里最终推荐 A、B 还是 tie。
8. regression-check 是否超过你给 CI 设置的阈值。

## 7. 当前限制

- CLI `replay` 是确定性回放：复用已记录的 tool/model 输出生成新 trace，也可以通过 `--plan` 使用编辑后的工具 Mock。
- CLI `replay-adapter` 可以显式执行本地 Python callable，并生成新的真实执行 trace。
- CLI `replay-compare` 可以对比原始 trace 片段和 replay trace。
- CLI `regression-check` 可以作为 CI 门禁比较 baseline 和 candidate trace。
- SDK 已有通用 `CallableAgentAdapter`，可以调用真实 Python callable 生成新 trace。
- SDK 已有 `LangGraphAdapter`，可以调用编译后的 LangGraph graph 的 `invoke`；打开 `trace_stream=True` 后，也可以把每个 LangGraph node update 展开成单独 step。
- SDK 已有 `OpenAIAdapter`，支持 Responses API 和 Chat Completions，并会把 usage 映射成费用信息；设置 `expand_output_items=True` 后，可以把 Responses output item 展开成子 step。
- SDK 已有 `AnthropicAdapter`，支持 Claude Messages API，并会把 usage 映射成费用信息；设置 `expand_content_blocks=True` 后，可以把 content block 展开成子 step；传入 `tools={...}` 后，可以执行 Claude `tool_use` 请求里的本地 Python 工具，并把每轮模型调用和工具调用记录进 trace。
- 已有 OTLP JSON 文件导出，也可以直接推送到 OpenTelemetry Collector HTTP endpoint。
- 已有隐私扫描和自动脱敏开关，但如果你手动把密钥写进不常见字段，仍建议分享前跑一次 `privacy-scan`。
- Claude Code/Codex 的专用适配器还没实现。
- 还没有账号、团队权限、远程存储。
