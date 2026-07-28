# Claude Code HTTP Hooks 实时 Trace 接入

## 实现目标

Claude Code 在执行过程中将可见生命周期事件自动 POST 到 Agent DevTools。Agent DevTools 将同一 Claude Code `session_id` 汇总为一个 Trace，立即写入本地 SQLite 和 `traces/`，并通过 SSE 推送到 Web UI。整个过程不需要手动上传 `.trace.json`。

本接入只记录 Claude Code hooks 暴露的事件，不记录隐藏推理或平台内部遥测。

## 数据流

```text
Claude Code
  -> HTTP hooks
  -> POST /api/hooks/claude-code
  -> ClaudeHookIngestor
  -> redacted SQLite + atomic trace.json
  -> SSE /api/live/traces
  -> React Web UI
```

## 1. 安装项目依赖

在 Agent DevTools 项目根目录执行：

```powershell
py -m pip install -e ".[dev]"
cd packages\web-ui
npm install
cd ..\..
```

## 2. 启动本地 Trace API

终端 A：

```powershell
py packages\cli\agent_devtools_cli\main.py serve --root . --port 8791
```

服务只监听 `127.0.0.1`。可用下面的命令检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8791/api/health
```

## 3. 启动 Web UI

终端 B：

```powershell
cd packages\web-ui
npm run dev
```

打开 Vite 输出的地址，通常是 `http://127.0.0.1:5173/`。Vite 会把 `/api` 代理到 `http://127.0.0.1:8791`。

页面启动后会建立 `EventSource('/api/live/traces')`。Claude Code 的新事件到达后，页面会自动选择对应运行并刷新 Timeline，无需导入文件。

## 4. 启动 Claude Code

项目已经包含 `.claude/settings.json`。请从 Agent DevTools 项目根目录启动 Claude Code，使项目级 hooks 配置生效：

```powershell
claude
```

提交一个会使用工具的任务，例如：

```text
读取 README.md，并告诉我项目的本地启动命令。
```

## 5. 实时事件映射

| Claude Code hook | Agent DevTools 行为 |
|---|---|
| `SessionStart` | 创建或重新打开 Claude Code session Trace |
| `UserPromptSubmit` | 更新 Trace 任务标题并增加 `User prompt` 步骤 |
| `PreToolUse` | 创建尚未结束的 `tool_call` 步骤 |
| `PostToolUse` | 按 `tool_use_id` 完成同一个工具步骤并记录结果 |
| `PostToolUseFailure` | 完成工具步骤并记录 `ClaudeToolError` |
| `Stop` | 写入 `last_assistant_message` 并完成当前运行 |
| `SessionEnd` | 在没有额外输出时完成 session Trace |

Claude Code 的原始 `session_id` 不会作为 run id 保存。Agent DevTools 使用它的 SHA-256 摘要生成稳定的 `claude-code-...` run id。`cwd` 只记录是否存在，不保存原始绝对路径，避免 Trace 文件写入个人目录信息。

## 6. 存储与隐私

每个 hook 到达后都会执行以下顺序：

1. 将事件映射为 Trace/Step。
2. 原子更新 `traces/claude-code-*.trace.json`。
3. 使用现有脱敏规则更新 `.agent-devtools/traces.db`。
4. 从脱敏后的 SQLite 重新读取 Trace。
5. 只把脱敏后的 Trace 推送给 Web UI。

因此 Trace 文件、SQLite 和实时 SSE 使用同一套脱敏边界。服务默认只绑定本机 loopback，不应直接暴露到公网。

## 7. 不启动 Web UI 时

HTTP hooks 仍会继续采集。之后启动 Web UI，页面会从本地 SQLite 加载已经保存的 Trace。也可以用 CLI 检查：

```powershell
py packages\cli\agent_devtools_cli\main.py store list --db .agent-devtools\traces.db
```

## 8. Replay

`UserPromptSubmit` 会被记录为可回放检查点。旧版本已经采集的 Claude Code Trace 也会在 Web UI 中兼容识别 `User prompt`，不需要重新上传或重录。

Replay 工作台提供两种命令：

- 确定性 Replay：复用已记录的步骤和工具结果，不调用 Claude Code。
- Claude Code 真实重跑：启动一个新的非交互 Claude Code session；新 session 的 hooks 会继续实时写入 Agent DevTools，并带上原 Trace 和起点步骤关联。

本地 Trace 的真实重跑命令使用 SQLite 中的 run id，不依赖带哈希的 Trace 文件名：

```powershell
py packages\cli\agent_devtools_cli\main.py replay-claude-code --run-id <run-id> --start-step <step-id> --root . --allow-agent-execution
```

安全默认值如下：

- 没有 `--allow-agent-execution` 时拒绝启动 Claude Code。
- API 地址只允许带明确端口的 loopback HTTP 地址。
- 默认使用 Claude Code `plan` 权限模式。
- 默认最高预算为 1 USD，可用 `--max-budget-usd` 调低。
- 可重复传入 `--allowed-tool` 缩小工具范围。

例如只允许读取和文件匹配，并把预算限制为 0.25 USD：

```powershell
py packages\cli\agent_devtools_cli\main.py replay-claude-code --run-id <run-id> --start-step <step-id> --root . --allowed-tool Read --allowed-tool Glob --max-budget-usd 0.25 --allow-agent-execution
```

真实重跑完成后，新 Trace 的标签包含 `replay=true`、`replay_mode=claude_code_execution`、`source_run_id` 和 `source_start_step_id`，可以在 Replay Compare 中与原路径比较。

如果 Claude Code 返回 `error_max_budget_usd`，说明真实重跑已经触发预算上限。Agent DevTools 会保留已经实时采集到的部分步骤，并把新 Trace 标记为 `status=error`、`partial=true`、`claude_result_subtype=error_max_budget_usd`，同时记录 Claude 返回的 `total_cost_usd` 和 token 用量。预算检查发生在 Claude Code API 调用之后，因此实际成本可能略高于 `--max-budget-usd`。

是否提高预算需要人工确认成本。常见选择是先扩大到 0.50 或 0.75 USD，并继续用 `--permission-mode plan`、`--allowed-tool` 限制工具范围；不想继续付费时，可以直接查看这个部分 Trace 或使用确定性 Replay。

## 9. 故障排查

### Claude Code 有任务，但 Web 没有更新

先检查 API：

```powershell
Invoke-RestMethod http://127.0.0.1:8791/api/health
```

再确认 Claude Code 是从包含 `.claude/settings.json` 的项目根目录启动。

### API 已收到 Trace，但页面不更新

检查 Vite 终端是否仍在运行，并确认浏览器访问的是 Vite 本地地址，不是 GitHub Pages。GitHub Pages 无法连接电脑上的 loopback API。

### Agent DevTools 服务未启动

Claude Code 的 HTTP hook 会报告非阻塞连接错误，Claude Code 任务仍可继续。启动 API 后，后续 hook 才会被采集；未送达的旧 hook 不会自动补发。

### 端口 8791 被占用

修改 `.claude/settings.json` 中全部 hook URL，同时用相同端口启动 `serve`。Web UI 启动前设置：

```powershell
$env:AGENT_DEVTOOLS_API_URL = "http://127.0.0.1:8792"
npm run dev
```

## 10. 验证命令

```powershell
py -m pytest packages\python-sdk\tests\test_local_api.py -q
cd packages\web-ui
npm run test:data
npm run build
```

后端测试覆盖 Claude Code prompt、工具调用关联、Stop 完成和 SSE 推送；前端测试覆盖实时订阅、Trace 标准化和连接清理。
