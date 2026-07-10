"""Tests for release guardrails that catch deployment footguns."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_release_guardrails import check_repo


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_release_guardrails_detect_frontend_localhost_3000(tmp_path: Path) -> None:
    _write(tmp_path / "packages/web-ui/src/api.ts", "fetch('http://localhost:3000/api/traces')\n")
    _write(tmp_path / "packages/web-ui/package.json", json.dumps({"dependencies": {}, "devDependencies": {}}))

    issues = check_repo(tmp_path)

    assert any(issue.code == "hardcoded-localhost-3000" for issue in issues)


def test_release_guardrails_detect_env_files_and_personal_paths(tmp_path: Path) -> None:
    _write(tmp_path / ".env", "OPENAI_API_KEY=sk-live-secret\n")
    _write(tmp_path / "docs/setup.md", "Use C:\\Users\\alice\\project\\trace.json locally.\n")
    _write(tmp_path / "packages/web-ui/package.json", json.dumps({"dependencies": {}, "devDependencies": {}}))

    issues = check_repo(tmp_path)

    assert any(issue.code == "env-file" for issue in issues)
    assert any(issue.code == "personal-path" for issue in issues)
    assert all("sk-live-secret" not in issue.message for issue in issues)


def test_release_guardrails_detect_undeclared_web_imports(tmp_path: Path) -> None:
    _write(
        tmp_path / "packages/web-ui/package.json",
        json.dumps({"dependencies": {"react": "1.0.0"}, "devDependencies": {}}),
    )
    _write(
        tmp_path / "packages/web-ui/src/App.tsx",
        "import React from 'react';\nimport debounce from 'lodash';\n",
    )

    issues = check_repo(tmp_path)

    assert any(issue.code == "undeclared-web-import" and "lodash" in issue.message for issue in issues)
    assert all("react" not in issue.message for issue in issues)
