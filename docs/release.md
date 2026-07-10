# Release Guide

This guide describes how to verify, build, and publish Agent DevTools.

The project currently ships as a Python package with a console script named
`agent-devtools`. The Web UI is a local Vite app that is started from source.

## Release Checklist

Run from the repository root unless a command says otherwise.

```powershell
git status --short
py -m pytest

cd packages\web-ui
npm ci --ignore-scripts --registry=https://registry.npmjs.org/
npm audit --audit-level=high --registry=https://registry.npmjs.org/
npm run test:data
npm run lint
npm run build
cd ..\..
```

Build the Python package:

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
py -m pip install build wheel
py -m build
```

Smoke-test the console script in editable mode:

```powershell
py -m pip install -e .
agent-devtools --help
agent-devtools list traces
```

On Windows, if the console script directory is not on `PATH`, run the generated
`agent-devtools.exe` by full path or add Python's `Scripts` directory to `PATH`.

Inspect the generated artifacts:

```powershell
Get-ChildItem dist
```

If `twine` is installed, validate the package metadata:

```powershell
py -m twine check dist/*
```

## Versioning

Update these files together before a release:

- `pyproject.toml`
- `CHANGELOG.md`
- `README.md`, if install or usage behavior changed

For pre-1.0 releases:

- Increment patch for fixes, docs, packaging, security defaults, and internal hardening.
- Increment minor for new user-facing features that keep the trace schema compatible.
- Document any trace schema changes clearly before publishing.

## Publishing

Publishing to PyPI is intentionally manual until package ownership and naming are finalized.

```powershell
py -m twine upload dist/*
```

Do not upload traces, `.env` files, local SQLite databases, OTLP exports, or replay plans.
The `.gitignore` is configured to keep those local artifacts out of normal commits.

## GitHub Release

After publishing or tagging a source release:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

Create a GitHub Release from that tag and paste the matching `CHANGELOG.md` section into
the release notes.
