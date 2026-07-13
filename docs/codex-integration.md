# Codex Integration

Agent DevTools can make local traces available to Codex through its stdio MCP server.
Initialize the workspace in the project that writes traces, then start the server from
that project root:

```powershell
py packages\cli\agent_devtools_cli\main.py init
py packages\cli\agent_devtools_cli\main.py mcp
```

Print a generic stdio server descriptor with an absolute CLI source path for the same workspace:

```powershell
py packages\cli\agent_devtools_cli\main.py mcp-config --root .
```

Register the returned command descriptor as a stdio MCP server in the Codex environment.
The descriptor intentionally does not assume a particular Codex configuration-file schema.
After adding the descriptor, restart Codex so it discovers the new MCP server.
The server offers:

- `list_recent_traces`: imports newly written `.trace.json` files and lists indexed runs.
- `analyze_trace`: returns cost, latency, failure, retry, and loop analysis for one run.
- `compare_traces`: produces an A/B experiment report for two run IDs.
- `record_external_audit`: writes an explicit audit trace for visible operations supplied by the caller.

The MCP server reads and writes only the selected local workspace. It indexes redacted
trace copies in `.agent-devtools/traces.db`. Keep a watcher running when another process
creates traces continuously:

```powershell
py packages\cli\agent_devtools_cli\main.py watch
```

Codex does not expose its hidden reasoning or internal telemetry to this project. Use
`record_external_audit` only for explicit visible actions such as a command invocation,
file edit, or task result.

For a terminal-only workflow, `audit` writes the same type of external audit trace:

```powershell
py packages\cli\agent_devtools_cli\main.py audit "Codex visible work" --event "run command" --error-event "read docs=403"
```

`--event` can be repeated for successful visible operations. Repeat `--error-event`
with `name=message` for failed visible operations. Neither option captures Codex
reasoning or hidden platform telemetry.
