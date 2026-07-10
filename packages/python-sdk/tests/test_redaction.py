"""Tests for trace privacy redaction."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent_devtools import Cost, RedactionConfig, Step, ToolCall, TraceWriter, new_run, redact_trace, scan_trace_for_secrets


def _sensitive_trace():
    trace = new_run("Email alice@example.com using api_key sk-live-secret")
    trace.run.labels["owner_email"] = "alice@example.com"
    trace.run.final_output = {
        "message": "sent to alice@example.com",
        "password": "hunter2",
    }
    step = Step(
        type="tool_call",
        name="crm.lookup",
        input={"email": "alice@example.com", "nested": {"access_token": "token-123"}},
        output={"customer": "alice@example.com", "ok": True},
        tool=ToolCall(
            name="crm.lookup",
            args={"api_key": "sk-live-secret", "query": "alice@example.com"},
            result={"authorization": "Bearer secret-token", "name": "Alice"},
        ),
        cost=Cost(total_tokens=12),
    )
    step.complete()
    trace.add_step(step)
    return trace


def test_redact_trace_redacts_sensitive_keys_and_email_strings() -> None:
    trace = _sensitive_trace()

    redacted = redact_trace(trace)
    data = redacted.to_dict()

    assert data["run"]["labels"]["owner_email"] == "[REDACTED]"
    assert data["run"]["final_output"]["message"] == "sent to [REDACTED]"
    assert data["run"]["final_output"]["password"] == "[REDACTED]"
    assert data["steps"][0]["input"]["email"] == "[REDACTED]"
    assert data["steps"][0]["input"]["nested"]["access_token"] == "[REDACTED]"
    assert data["steps"][0]["tool"]["args"]["api_key"] == "[REDACTED]"
    assert data["steps"][0]["tool"]["args"]["query"] == "[REDACTED]"
    assert data["steps"][0]["tool"]["result"]["authorization"] == "[REDACTED]"
    assert data["steps"][0]["output"]["customer"] == "[REDACTED]"
    assert trace.steps[0].tool.args["api_key"] == "sk-live-secret"


def test_redaction_config_can_preserve_email_like_values() -> None:
    trace = _sensitive_trace()

    redacted = redact_trace(trace, RedactionConfig(redact_emails=False))
    data = redacted.to_dict()

    assert data["steps"][0]["tool"]["args"]["api_key"] == "[REDACTED]"
    assert data["steps"][0]["input"]["email"] == "alice@example.com"


def test_trace_writer_can_write_redacted_trace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        writer = TraceWriter(tmp, redaction=True)
        path = writer.write(_sensitive_trace())

        data = json.loads(Path(path).read_text(encoding="utf-8"))

    assert data["steps"][0]["tool"]["args"]["api_key"] == "[REDACTED]"
    assert data["steps"][0]["tool"]["result"]["authorization"] == "[REDACTED]"


def test_scan_trace_for_secrets_reports_paths_without_values() -> None:
    findings = scan_trace_for_secrets(_sensitive_trace())

    paths = {finding.path for finding in findings}
    kinds = {finding.kind for finding in findings}
    rendered = json.dumps([finding.to_dict() for finding in findings])

    assert "run.task" in paths
    assert "steps[0].tool.args.api_key" in paths
    assert "steps[0].tool.result.authorization" in paths
    assert {"email", "sensitive_key", "api_key_like", "bearer_token"} <= kinds
    assert "sk-live-secret" not in rendered
    assert "alice@example.com" not in rendered
    assert "Bearer secret-token" not in rendered


def test_trace_writer_redacts_automatically_when_env_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DEVTOOLS_REDACT_ON_WRITE", "true")
    with tempfile.TemporaryDirectory() as tmp:
        writer = TraceWriter(tmp)
        path = writer.write(_sensitive_trace())

        data = json.loads(Path(path).read_text(encoding="utf-8"))

    assert data["run"]["labels"]["owner_email"] == "[REDACTED]"
    assert data["steps"][0]["tool"]["args"]["api_key"] == "[REDACTED]"


def test_redact_trace_covers_common_cloud_and_repo_secret_shapes() -> None:
    trace = new_run("Secret scan")
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    github_token = "ghp_" + ("x" * 36)
    slack_token = "xoxb-" + ("1" * 12) + "-" + ("2" * 12) + "-" + ("a" * 24)
    jwt = "eyJ" + ("a" * 12) + "." + ("b" * 12) + "." + ("c" * 12)
    private_key = "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----"
    trace.run.final_output = {
        "token": "opaque-token",
        "cookie": "session=secret",
        "aws": aws_key,
        "github": github_token,
        "slack": slack_token,
        "jwt": jwt,
        "pem": private_key,
    }

    redacted = redact_trace(trace).to_dict()
    rendered = json.dumps(redacted, ensure_ascii=False)

    assert redacted["run"]["final_output"]["token"] == "[REDACTED]"
    assert redacted["run"]["final_output"]["cookie"] == "[REDACTED]"
    assert aws_key not in rendered
    assert github_token not in rendered
    assert slack_token not in rendered
    assert jwt not in rendered
    assert private_key not in rendered


def test_scan_trace_reports_common_secret_shapes_without_values() -> None:
    trace = new_run("Secret scan")
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    github_token = "ghp_" + ("x" * 36)
    jwt = "eyJ" + ("a" * 12) + "." + ("b" * 12) + "." + ("c" * 12)
    trace.run.final_output = {
        "aws": aws_key,
        "github": github_token,
        "jwt": jwt,
    }

    findings = scan_trace_for_secrets(trace)
    kinds = {finding.kind for finding in findings}
    rendered = json.dumps([finding.to_dict() for finding in findings])

    assert {"aws_access_key", "github_token", "jwt"} <= kinds
    assert aws_key not in rendered
    assert github_token not in rendered
    assert jwt not in rendered
