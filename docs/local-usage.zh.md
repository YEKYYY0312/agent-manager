# Agent DevTools 本地使用教程

本教程面向想在本机记录、查看和分析 AI Agent 运行的人。Agent DevTools 的核心产物是 `*.trace.json`：一次 Agent 运行对应一条 Trace，Trace 内含 Run、步骤、耗时、状态、模型用量和成本。

## 先理解边界

| 你要看的数据 | 能否记录 | 方法 |
|---|---|---|
| 自己 Agent 的模型 Token、成本、真实耗时 | 可以 | 在实际调用处接入 Python/TS SDK 或适配器 |
| 自己 Agent 的工具调用、异常和重试 | 可以 | `traced_tool`、`traced_step` 或适配器 |
| Codex 本机 session 的可见消息、工具调用和 Token 用量 | 可以 | 启动本地 API 后自动增量读取 Codex session JSONL |
| Codex 隐藏推理、system/developer instructions 和平台内部费用 | 不可以 | 适配器明确忽略隐藏上下文，本机日志不提供金额 |

审计 Trace 只说明做过哪些显式操作，Token、成本和耗时会是零。不要把它当作模型调用 Trace。

## 1. 安装和初始化

在你的 Agent 项目根目录执行：

```powershell
py -m pip install -e "C:\path\to\agent-manager"
agent-devtools init
agent-devtools doctor
```

`init` 创建 `traces/`、`.agent-devtools/config.json` 和本地 SQLite 索引；`doctor` 用来确认路径、示例 Trace 与索引可用。

## 2. 启动本地查看器

在 Agent DevTools 仓库根目录执行一条命令，同时启动本地数据 API 和 Web UI：

```powershell
cd C:\path\to\agent-manager
agent-devtools start --root .
```

打开 `http://127.0.0.1:5175/`。另开一个终端可检查两个服务是否正常：

```powershell
agent-devtools health
```

`start` 默认使用 API 端口 `8791` 和 Web 端口 `5175`；服务已经健康时会直接复用，不会重复启动。按 `Ctrl+C` 时，只停止本次命令创建的进程。页面会通过 loopback API 自动发现 `traces/` 中的新文件，并读取已脱敏的本地索引。

若需要分别排查 API 和 Web，可在两个终端手动执行：

```powershell
agent-devtools serve --root . --port 8791
npm --prefix packages\web-ui run dev -- --host 127.0.0.1 --port 5175
```

GitHub Pages 是静态页面，不能自动读取你电脑的 Trace；在那里只能手动导入文件。

## 3. 记录真实 Python Agent

给 Agent 顶层函数和模型调用各加一层装饰器：

```python
from agent_devtools import traced_agent, traced_model, traced_tool

@traced_model("answer", model="gpt-4.1-mini")
def ask_model(prompt: str):
    # 返回 OpenAI/Anthropic SDK 的原始响应，或含 usage 的 dict。
    return client.responses.create(model="gpt-4.1-mini", input=prompt)

@traced_tool("customer.lookup")
def lookup_customer(customer_id: str):
    return database.get(customer_id)

@traced_agent("Customer support reply", output_dir="traces")
def run_agent(question: str):
    customer = lookup_customer("customer-1")
    return ask_model(f"{question}\nCustomer: {customer}")

run_agent("Summarize the account status")
```

`traced_agent` 管理整次 Run 的开始、结束和写文件；`traced_model` 计量调用耗时并从响应 `usage` 提取输入/输出 Token；`traced_tool` 记录工具参数、结果和异常。已知模型会按内置价格表估算美元成本，API 响应若返回金额则优先使用它。

运行后刷新 Web UI，选择新 Trace：

- `Timeline` 看步骤顺序和状态。
- `Analysis` 看最慢步骤、最高成本步骤、失败、循环和重试。
- `Diff` 比较两次运行。
- `Replay` 从一个记录的检查点创建确定性回放。

## 4. 记录 TypeScript/Node Agent

```ts
import { traceAgent } from '@agent-devtools/sdk';

await traceAgent('Customer support reply', async (trace) => {
  const customer = await trace.tool('customer.lookup', { id: 'customer-1' }, () => lookupCustomer('customer-1'));
  return trace.model('answer', { question: 'Summarize account', customer }, async () => {
    const response = await client.responses.create({ model: 'gpt-4.1-mini', input: '...' });
    return response.output_text;
  }, {
    model: 'gpt-4.1-mini',
    cost: { input_tokens: 420, output_tokens: 36, amount_usd: 0.0000312 },
  });
}, { outputDir: 'traces' });
```

Node SDK 会自动测量 Run 和步骤耗时；由于各 SDK 返回结构不同，Token/金额由调用方从响应 `usage` 映射到 `cost`。

## 5. 用 Codex 查看本地 Trace

在 Codex 配置中注册 `agent-devtools mcp` 后重启 Codex。可以直接提出：

```text
使用 list_recent_traces 列出最新 10 条 Trace。
使用 analyze_trace 分析 Run ID <run-id>。
使用 compare_traces 对比 <left-run-id> 和 <right-run-id>。
```

Codex 查询的是同一个本地 SQLite 索引。启动 `agent-devtools serve` 后，API 还会自动检测 Codex session 目录，并把本机 Codex 的可见消息和工具调用实时写入同一索引。完整启用流程见 [Codex 实时 Trace 接入](codex-live-sessions.zh.md)。

若只想手动记录一条外部工作摘要，也可以使用：

```powershell
agent-devtools audit "Codex visible work" --event "inspect traces" --event "run tests"
```

`audit` 生成的仍是由你显式提供事件的摘要，不等同于自动 session Trace。

## 6. 常用排查流程

```powershell
agent-devtools list traces
agent-devtools show traces\<file>.trace.json --detail
agent-devtools analyze traces\<file>.trace.json
agent-devtools diff traces\<left>.trace.json traces\<right>.trace.json
agent-devtools privacy-scan traces\<file>.trace.json
```

建议顺序是：先看 Run 是否失败，再看第一个失败步骤和错误详情，然后看最慢步骤与成本最高的模型步骤，最后用 Diff 找两次运行的第一个分叉点。

## 7. 隐私和共享

Trace 可能含 prompt、工具参数和输出。分享前运行：

```powershell
agent-devtools privacy-scan traces\<file>.trace.json
agent-devtools redact traces\<file>.trace.json --output traces\shared.safe.trace.json
```

设置 `$env:AGENT_DEVTOOLS_REDACT_ON_WRITE = "true"` 可在 SDK 写入时默认脱敏。不要把真实 API 密钥、数据库 URL 或原始敏感 Trace 提交到 GitHub。
