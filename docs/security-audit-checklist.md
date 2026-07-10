# Security Audit Checklist

Use this checklist before calling the project release-ready.

## High-Risk Surfaces

- `packages/cli/agent_devtools_cli/main.py`
  - `replay-adapter` executes local Python code only with `--allow-unsafe-code`.
  - `--pythonpath` must not permanently pollute the parent process.
  - `store import` should avoid unbounded recursive scans unless `--recursive` is set.
- `packages/python-sdk/agent_devtools/otel.py`
  - OTLP endpoints must block unsafe private/insecure destinations by default.
  - TLS verification must stay enabled by default.
- `packages/python-sdk/agent_devtools/adapters.py`
  - Request options must not override transport credentials or base URLs.
  - Tool calls must require structured object inputs before `**kwargs` execution.
- `packages/web-ui/src/storage.ts`
  - localStorage stores only import metadata.
  - IndexedDB stores local user-imported trace content; this is local-first persistence, not cloud sync.
- `packages/web-ui/src/trace.ts`
  - Browser fetches are restricted to bundled `/traces/*.trace.json` assets.
  - Imported files are size-limited before parsing.

## Privacy

- Run `agent-devtools privacy-scan` on traces before sharing.
- Use `agent-devtools redact` or `--redact` for storage/export/push workflows.
- Check that new adapters use shared redaction and scanning helpers where payloads may leave the local machine.

## Verification Commands

```powershell
py -m pytest

cd packages\web-ui
npm ci --ignore-scripts --registry=https://registry.npmjs.org/
npm audit --audit-level=high --registry=https://registry.npmjs.org/
npm run test:data
npm run lint
npm run build
```

## Release Gate

- No unreviewed local code execution path.
- No default remote export of prompt/tool payloads.
- No persistent browser storage of trace payloads except IndexedDB local imports.
- No GitHub Actions write token unless a workflow explicitly needs it.
- No `.env`, `.npmrc`, SQLite DB, OTLP export, trace, or replay plan committed.

