"""Release guardrails for deployment-footgun checks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", "build", "dist", "node_modules"}
EXCLUDED_PARTS = {"tests"}
EXCLUDED_FILES = {"scripts/check_release_guardrails.py"}
ALLOWED_ENV_FILES = {".env.example"}
LOCALHOST_3000_RE = re.compile(r"\b(?:https?://)?(?:localhost|127\.0\.0\.1):3000\b")
PERSONAL_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\Users\\[^\\\s]+|/Users/[^/\s]+|/home/[^/\s]+|\bAdministrator\b|\bADMINI~1\b|\bmyname\b)",
    re.IGNORECASE,
)
IMPORT_RE = re.compile(
    r"(?:import|export)\s+(?:[^'\"\n]+?\s+from\s+)?['\"]([^'\"]+)['\"]|require\(\s*['\"]([^'\"]+)['\"]\s*\)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class GuardrailIssue:
    code: str
    path: str
    message: str


def check_repo(root: str | Path) -> list[GuardrailIssue]:
    root_path = Path(root)
    issues: list[GuardrailIssue] = []
    for path in _iter_files(root_path):
        relative = _relative(path, root_path)
        issues.extend(_check_path(relative))
        if _is_text_file(path):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            issues.extend(_check_text(relative, text))
    issues.extend(_check_web_imports(root_path))
    return issues


def _check_path(path: str) -> list[GuardrailIssue]:
    name = Path(path).name
    if name.startswith(".env") and name not in ALLOWED_ENV_FILES:
        return [GuardrailIssue("env-file", path, f"{path}: environment files must not be committed")]
    return []


def _check_text(path: str, text: str) -> list[GuardrailIssue]:
    issues: list[GuardrailIssue] = []
    if LOCALHOST_3000_RE.search(text):
        issues.append(GuardrailIssue("hardcoded-localhost-3000", path, f"{path}: hard-coded localhost:3000 is not allowed"))
    if PERSONAL_PATH_RE.search(text):
        issues.append(GuardrailIssue("personal-path", path, f"{path}: personal absolute paths or usernames are not allowed"))
    return issues


def _check_web_imports(root: Path) -> list[GuardrailIssue]:
    web_root = root / "packages" / "web-ui"
    package_json = web_root / "package.json"
    if not package_json.exists():
        return []
    declared = _declared_web_packages(package_json)
    issues: list[GuardrailIssue] = []
    for path in [web_root / "vite.config.ts", *sorted((web_root / "src").rglob("*"))]:
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        for specifier in _bare_imports(text):
            package_name = _package_name(specifier)
            if package_name not in declared:
                relative = _relative(path, root)
                issues.append(
                    GuardrailIssue(
                        "undeclared-web-import",
                        relative,
                        f"{relative}: bare import '{package_name}' is not declared in packages/web-ui/package.json",
                    )
                )
    return issues


def _declared_web_packages(package_json: Path) -> set[str]:
    data = json.loads(package_json.read_text(encoding="utf-8"))
    declared: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = data.get(key, {})
        if isinstance(values, dict):
            declared.update(str(name) for name in values)
    return declared


def _bare_imports(text: str) -> Iterable[str]:
    for match in IMPORT_RE.finditer(text):
        specifier = match.group(1) or match.group(2) or ""
        if not specifier:
            continue
        if specifier.startswith((".", "/", "node:", "http://", "https://")):
            continue
        yield specifier


def _package_name(specifier: str) -> str:
    parts = specifier.split("/")
    if specifier.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() in EXCLUDED_FILES:
            continue
        relative_parts = relative.parts
        if any(part in EXCLUDED_DIRS or part in EXCLUDED_PARTS for part in relative_parts):
            continue
        yield path


def _is_text_file(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in {".gitignore", ".env", ".env.example"}


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check release guardrails")
    parser.add_argument("root", nargs="?", default=".", help="Repository root")
    args = parser.parse_args(argv)
    issues = check_repo(Path(args.root))
    for issue in issues:
        print(f"{issue.code}: {issue.message}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
