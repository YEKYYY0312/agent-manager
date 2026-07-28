# Codex 实时 Trace 接入

Agent DevTools 可以直接跟随 Codex 在本机追加的 session JSONL，将用户可见事件转换为 Trace，并通过现有 SQLite、`traces/` 和 SSE 链路实时显示在 Web UI。这个流程不需要手动上传 `.trace.json`，也不需要在 Codex 中配置 HTTP hook。

## 工作流

```text
Codex 桌面端或 CLI
  -> 本机 .codex/sessions/**/rollout-*.jsonl
  -> CodexSessionWatcher 增量读取完整 JSON 行
  -> CodexSessionIngestor 转换可见事件
  -> SQLite + traces/*.trace.json
  -> SSE /api/live/traces
  -> Web Timeline 和 Step Inspector
```

API 启动时优先读取 `$env:CODEX_HOME\sessions`；若未设置 `CODEX_HOME`，则读取当前用户目录下的 `.codex\sessions`。启动前 15 分钟内仍在活动的 session 会从头建立 Trace，旧文件只记录启动后新追加的事件，因此不会在每次启动时批量导入全部历史任务。

## 启动

先停止旧的 Agent DevTools API，再从项目根目录重新启动：

```powershell
py packages\cli\agent_devtools_cli\main.py serve --root . --port 8791
```

启动成功时会看到两行：

```text
Local Trace API listening at http://127.0.0.1:8791
Codex session watcher: <检测到的 sessions 目录>
```

另开一个终端启动 Web：

```powershell
npm.cmd --prefix packages\web-ui run dev -- --host 127.0.0.1 --port 5175
```

打开 `http://127.0.0.1:5175/`。之后正常使用 Codex 即可；新 prompt、可见回复、工具调用、工具结果和任务结束事件会自动更新当前 Trace，页面不需要刷新或上传文件。

## 事件映射

| Codex session 事件 | Agent DevTools 行为 |
|---|---|
| `task_started` | 重新打开当前 session Trace |
| `user_message` | 更新 Trace 标题并加入 `User prompt` 步骤 |
| assistant `message` | 加入 `Assistant commentary` 或 `Assistant answer` 模型步骤 |
| `function_call` / `custom_tool_call` | 创建工具步骤 |
| 对应的 `*_output` | 完成工具步骤并记录输出或错误 |
| `token_count` | 更新累计输入、输出和总 Token；本机日志未提供金额，因此金额保持 0 |
| `task_complete` | 将运行标记为成功并保存最终回复 |
| `turn_aborted` | 将运行标记为取消 |

同一个 Codex session 始终使用稳定的 `codex-<session-id>` run id。服务重启后会使用日志偏移生成稳定步骤 id，不会重复添加已经采集的步骤。

## 隐私边界

适配器只采集用户可见内容，明确忽略：

- `session_meta` 中的 system/developer instructions、动态工具定义和完整上下文；
- `reasoning`、`agent_reasoning` 和压缩上下文；
- 未识别的内部事件。

写入 Trace 和 SQLite 前仍会执行 Agent DevTools 的默认脱敏。单条可见消息、工具参数或工具输出最多保留 20,000 个字符，避免异常大的 session 输出拖垮本地页面。API 仍只绑定 `127.0.0.1`。

## 当前边界

- 这是对本机 Codex session 持久化事件的增量适配，不是 Codex 官方逐步 HTTP hook。Codex 升级若修改 JSONL 事件结构，需要同步更新适配器；未知事件会被安全忽略。
- 当前实现用于实时可视化，不把 Codex prompt 标记为 replayable，也不会自动重新执行 Codex。Web 中的 Codex Trace 暂不提供真实 Replay。
- 只能采集本机可访问的 Codex session。云端任务若未同步到本机 session 目录，不会出现在 Web 中。

## 验收

1. API 终端显示 `Codex session watcher`。
2. Web 保持打开并显示 `LIVE`。
3. 在 Codex 中发送一个新 prompt，并让它至少执行一次工具调用。
4. Web 自动出现 `codex-...` Trace，Timeline 依次增加用户消息、回复和工具步骤。
5. Codex 完成后，Trace 出现结束时间和最终回复。

可用以下命令验证回归：

```powershell
py -m pytest packages\python-sdk\tests\test_local_api.py -q
```
