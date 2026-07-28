# Cursor Agent Hooks 实时 Trace 接入

## 工作流

```text
Cursor Agent
  -> 用户级 command hooks (stdin/stdout JSON)
  -> scripts/cursor_hook_forward.py
  -> POST http://127.0.0.1:8791/api/hooks/cursor
  -> CursorHookIngestor
  -> SQLite + traces/cursor-*.trace.json
  -> SSE /api/live/traces
  -> Web UI
```

Cursor 使用稳定的 `conversation_id` 将多轮事件归入同一个 Trace。Agent DevTools 只记录 Cursor hooks 明确提供的可见事件，不读取隐藏推理；配置没有注册 `afterAgentThought`。

## 采集事件

| Cursor hook | Agent DevTools 行为 |
| --- | --- |
| `beforeSubmitPrompt` | 更新当前任务并记录 `User prompt` |
| `preToolUse` | 创建工具步骤 |
| `postToolUse` | 完成工具步骤并记录结果和耗时 |
| `postToolUseFailure` | 记录工具错误、超时或取消 |
| `afterAgentResponse` | 记录可见回答并更新最终输出 |
| `stop` | 将运行标记为成功、取消或错误 |

`afterAgentThought` 故意不采集。`workspace_roots`、`transcript_path`、`user_email` 和原始 `conversation_id` 不写入 Trace 标签；run id 使用 `conversation_id` 的 SHA-256 摘要生成。

## 用户级配置

Cursor 桌面端从 `~/.cursor/hooks.json` 读取全局 hooks。用户级配置能覆盖本机打开的所有项目，结构如下：

```json
{
  "version": 1,
  "hooks": {
    "beforeSubmitPrompt": [{ "command": "py \"<repo>/scripts/cursor_hook_forward.py\"", "timeout": 5 }],
    "preToolUse": [{ "command": "py \"<repo>/scripts/cursor_hook_forward.py\"", "timeout": 5 }],
    "postToolUse": [{ "command": "py \"<repo>/scripts/cursor_hook_forward.py\"", "timeout": 5 }],
    "postToolUseFailure": [{ "command": "py \"<repo>/scripts/cursor_hook_forward.py\"", "timeout": 5 }],
    "afterAgentResponse": [{ "command": "py \"<repo>/scripts/cursor_hook_forward.py\"", "timeout": 5 }],
    "stop": [{ "command": "py \"<repo>/scripts/cursor_hook_forward.py\"", "timeout": 5 }]
  }
}
```

转发器只使用 Python 标准库。Agent DevTools API 不在线、连接失败或返回错误时，脚本仍以退出码 0 返回，不阻断 Cursor。它不会对 `preToolUse` 返回允许或拒绝决定，因此 Cursor 原有的工具审批策略保持不变。

Cursor 会监听 `hooks.json` 并在保存后重载。若没有重载，可重启 Cursor，然后在 **Customize > Hooks** 或 **Output > Cursor Hooks** 检查加载状态。

## 启动与验证

从 Agent DevTools 项目根目录启动 API：

```powershell
py packages\cli\agent_devtools_cli\main.py serve --root . --port 8791
```

启动 Web：

```powershell
cd packages\web-ui
npm run dev -- --host 127.0.0.1 --port 5175
```

在 Cursor Agent 中发送一个会调用工具的任务。网页应自动出现 `Cursor session`，Timeline 至少包含 `User prompt`、工具步骤和 `Assistant answer`，不需要上传 `trace.json`。

API 仍然只绑定 `127.0.0.1`。如需临时更换接收地址，可为 hook 进程设置 `AGENT_DEVTOOLS_CURSOR_HOOK_URL`，但不应将未认证接口暴露到公网。
