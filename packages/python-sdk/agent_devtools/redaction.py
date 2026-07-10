"""Privacy redaction helpers for trace data.

Redaction is intentionally local and deterministic: it walks trace-shaped data,
replaces sensitive fields, and returns a new Trace without mutating the source.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from .trace import Trace

REDACTED = "[REDACTED]"

DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "auth",
        "authorization",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "token",
        "bearertoken",
        "jwt",
        "password",
        "passwd",
        "secret",
        "credential",
        "credentials",
        "clientsecret",
        "privatekey",
        "accesskey",
        "sessionid",
        "sessiontoken",
        "cookie",
        "setcookie",
    }
)

EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}")
KEY_RE = re.compile(r"\b(?:sk|pk|ak)-[A-Za-z0-9_-]{8,}\b")
AUTH_HEADER_RE = re.compile(r"\b(?:Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE)
AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
GITHUB_TOKEN_RE = re.compile(r"\bgh[opsru]_[A-Za-z0-9_]{20,}\b")
SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,64}PRIVATE KEY-----[\s\S]{0,20000}?-----END [A-Z0-9 ]{0,64}PRIVATE KEY-----",
)

SECRET_VALUE_PATTERNS = (
    ("api_key_like", KEY_RE),
    ("bearer_token", AUTH_HEADER_RE),
    ("aws_access_key", AWS_ACCESS_KEY_RE),
    ("github_token", GITHUB_TOKEN_RE),
    ("slack_token", SLACK_TOKEN_RE),
    ("jwt", JWT_RE),
    ("private_key", PRIVATE_KEY_RE),
)


@dataclass(frozen=True)
class RedactionConfig:
    """Controls how trace values are redacted."""

    replacement: str = REDACTED
    redact_emails: bool = True
    redact_api_key_like_values: bool = True
    sensitive_keys: frozenset[str] = field(default_factory=lambda: DEFAULT_SENSITIVE_KEYS)


@dataclass(frozen=True)
class SecretFinding:
    """A location-only sensitive data finding.

    The raw value is intentionally never stored on the finding.
    """

    path: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind}


def normalize_redaction_config(redaction: bool | RedactionConfig | None) -> RedactionConfig | None:
    """Resolve caller/env redaction settings to a concrete config."""

    if redaction is True:
        return RedactionConfig()
    if isinstance(redaction, RedactionConfig):
        return redaction
    if redaction is None and redaction_env_enabled():
        return RedactionConfig()
    return None


def redaction_env_enabled() -> bool:
    return os.getenv("AGENT_DEVTOOLS_REDACT_ON_WRITE", "").strip().lower() in {"1", "true", "yes", "on"}


def redact_trace(trace: Trace, config: RedactionConfig | None = None) -> Trace:
    """Return a redacted copy of *trace* without mutating the original."""

    cfg = config or RedactionConfig()
    return Trace.from_dict(redact_value(trace.to_dict(), cfg))


def redact_value(value: Any, config: RedactionConfig | None = None, key: str | None = None) -> Any:
    """Redact a JSON-like value using key names and string patterns."""

    cfg = config or RedactionConfig()
    if key is not None and _is_sensitive_key(key, cfg):
        return cfg.replacement

    if isinstance(value, dict):
        return {k: redact_value(v, cfg, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item, cfg) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, cfg) for item in value]
    if isinstance(value, str):
        return _redact_string(value, cfg)
    return value


def scan_trace_for_secrets(trace: Trace, config: RedactionConfig | None = None) -> list[SecretFinding]:
    """Return location-only findings for sensitive trace values."""

    return scan_value_for_secrets(trace.to_dict(), config or RedactionConfig())


def scan_value_for_secrets(
    value: Any,
    config: RedactionConfig | None = None,
    *,
    path: str = "",
    key: str | None = None,
) -> list[SecretFinding]:
    """Scan a JSON-like value without retaining secret material."""

    cfg = config or RedactionConfig()
    findings: list[SecretFinding] = []
    current_path = path or "$"

    if key is not None and _is_sensitive_key(key, cfg):
        findings.append(SecretFinding(path=current_path, kind="sensitive_key"))

    if isinstance(value, dict):
        for child_key, child_value in value.items():
            child_key_text = str(child_key)
            findings.extend(
                scan_value_for_secrets(
                    child_value,
                    cfg,
                    path=_join_path(current_path if path else "", child_key_text),
                    key=child_key_text,
                )
            )
        return findings

    if isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(
                scan_value_for_secrets(
                    item,
                    cfg,
                    path=f"{current_path}[{index}]",
                )
            )
        return findings

    if isinstance(value, tuple):
        for index, item in enumerate(value):
            findings.extend(
                scan_value_for_secrets(
                    item,
                    cfg,
                    path=f"{current_path}[{index}]",
                )
            )
        return findings

    if isinstance(value, str):
        findings.extend(_scan_string(value, cfg, current_path))

    return findings


def _is_sensitive_key(key: str, config: RedactionConfig) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in config.sensitive_keys:
        return True
    return normalized.endswith("apikey")


def _redact_string(value: str, config: RedactionConfig) -> str:
    redacted = value
    if config.redact_emails:
        redacted = EMAIL_RE.sub(config.replacement, redacted)
    if config.redact_api_key_like_values:
        for _, pattern in SECRET_VALUE_PATTERNS:
            redacted = pattern.sub(config.replacement, redacted)
    return redacted


def _scan_string(value: str, config: RedactionConfig, path: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    if config.redact_emails and EMAIL_RE.search(value):
        findings.append(SecretFinding(path=path, kind="email"))
    if config.redact_api_key_like_values:
        for kind, pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                findings.append(SecretFinding(path=path, kind=kind))
    return findings


def _join_path(parent: str, key: str) -> str:
    if not parent:
        return key
    if _PLAIN_PATH_KEY_RE.fullmatch(key):
        return f"{parent}.{key}"
    return f"{parent}[{key!r}]"


_PLAIN_PATH_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
