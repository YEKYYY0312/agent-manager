# Findings

- Local P0 workbench is implemented on branch `codex/agent-manager-p0` through commit `64f859e`.
- The repository has a Python package, CLI entry point, local SQLite store, optional PostgreSQL store, stdio MCP server, and a Vite Web UI.
- The repository does not currently contain a standalone TypeScript SDK package, a reusable CI regression template, hosted role enforcement, retention controls, datasets, annotation storage, or failure clustering.
- GitHub push remains unavailable from this machine: HTTPS requests time out or require an unavailable proxy, and SSH has no authorized key.
- GitHub Pages is static and cannot directly access local Trace files; the loopback API is the supported local data path.
- Codex Desktop currently stores stdio MCP servers in `%USERPROFILE%\\.codex\\mcp.json`; Agent DevTools is registered there with the source CLI and this repository as its workspace root.
