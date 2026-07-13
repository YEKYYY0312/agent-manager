from __future__ import annotations

import json

import pytest

from agent_devtools import new_run
from agent_devtools.team import PostgresTeamRepository, ProjectTraceStore, TeamAccess, TeamProject, TeamTraceService, project_trace_key


def test_team_project_uses_hashed_tokens_and_enforces_roles() -> None:
    project = TeamProject.create("Support agents", admin_token="admin-secret")
    writer = project.issue_token("writer", TeamAccess.WRITER)
    reader = project.issue_token("reader", TeamAccess.READER)

    assert "admin-secret" not in project.to_dict().__repr__()
    assert project.authorize(writer, TeamAccess.WRITER).role is TeamAccess.WRITER
    assert project.authorize(reader, TeamAccess.READER).role is TeamAccess.READER
    with pytest.raises(PermissionError):
        project.authorize(reader, TeamAccess.WRITER)


def test_project_trace_keys_prevent_same_run_id_collisions() -> None:
    assert project_trace_key("project-a", "run-1") != project_trace_key("project-b", "run-1")
    assert project_trace_key("project-a", "run-1") == "project-a:run-1"


def test_project_trace_store_scopes_same_run_id_to_its_project() -> None:
    store = ProjectTraceStore()
    left = new_run("left")
    right = new_run("right")
    left.run.id = right.run.id = "shared-run"

    store.upsert("project-a", left)
    store.upsert("project-b", right)

    assert store.get("project-a", "shared-run").run.task == "left"
    assert store.get("project-b", "shared-run").run.task == "right"


def test_team_trace_service_enforces_role_and_retention() -> None:
    project = TeamProject.create("Support", admin_token="admin")
    writer = project.issue_token("writer", TeamAccess.WRITER)
    reader = project.issue_token("reader", TeamAccess.READER)
    service = TeamTraceService(project_id="support", project=project, store=ProjectTraceStore())
    trace = new_run("expired")

    service.upsert(writer, trace, retention_days=0)
    assert service.get(reader, trace.run.id) is not None
    with pytest.raises(PermissionError):
        service.purge_expired(reader)
    assert service.purge_expired("admin") == 1


def test_postgres_repository_declares_project_token_and_trace_tables() -> None:
    schema = PostgresTeamRepository.schema_sql()
    assert "CREATE TABLE IF NOT EXISTS team_projects" in schema
    assert "CREATE TABLE IF NOT EXISTS team_tokens" in schema
    assert "CREATE TABLE IF NOT EXISTS team_traces" in schema
    assert "PRIMARY KEY (project_id, run_id)" in schema


def test_postgres_repository_exposes_project_scoped_operations() -> None:
    for name in ["create_project", "issue_token", "authorize", "upsert_trace", "get_trace", "list_traces", "purge_expired"]:
        assert hasattr(PostgresTeamRepository, name), name


def test_postgres_repository_uses_project_scoped_sql(monkeypatch) -> None:
    fake = _FakePsycopg()
    monkeypatch.setattr("importlib.import_module", lambda name: fake if name == "psycopg" else None)
    repository = PostgresTeamRepository("postgresql://example/team")
    repository.create_project("support", "Support", admin_token="admin-secret")
    writer = repository.issue_token("support", "admin-secret", "writer", TeamAccess.WRITER)
    trace = new_run("ticket")
    repository.upsert_trace("support", writer, trace, retention_days=30)
    assert repository.get_trace("support", writer, trace.run.id).run.id == trace.run.id
    assert [item.run.id for item in repository.list_traces("support", writer, query="ticket")] == [trace.run.id]
    assert repository.purge_expired("support", "admin-secret") == 0
    rendered = "\n".join(query for query, _ in fake.calls)
    assert "project_id = %s" in rendered
    assert "ON CONFLICT (project_id, run_id)" in rendered
    assert "trace_json->'run'->>'task' ILIKE %s" in rendered
    assert "admin-secret" not in repr(fake.calls)


class _FakePsycopg:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple | None]] = []
        self.tokens: dict[tuple[str, str], tuple[str, int]] = {}
        self.traces: dict[tuple[str, str], dict] = {}

    def connect(self, *_args, **_kwargs):
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, db: _FakePsycopg) -> None:
        self.db = db

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def cursor(self): return _FakeCursor(self.db)


class _FakeCursor:
    def __init__(self, db: _FakePsycopg) -> None:
        self.db, self._row, self.rowcount = db, None, 0
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def execute(self, query, params=None):
        self.db.calls.append((query, params))
        if "INSERT INTO team_tokens" in query:
            self.db.tokens[(params[0], params[1])] = (params[2], params[3])
        elif query.startswith("SELECT member_name"):
            self._row = self.db.tokens.get((params[0], params[1]))
        elif "INSERT INTO team_traces" in query:
            self.db.traces[(params[0], params[1])] = json.loads(params[2])
        elif query.startswith("SELECT trace_json"):
            if "run_id" in query:
                value = self.db.traces.get((params[0], params[1])); self._row = (value,) if value else None
            else:
                values = [value for (project_id, _), value in self.db.traces.items() if project_id == params[0]]
                self._rows = [(value,) for value in values]
    def fetchone(self): return self._row
    def fetchall(self): return getattr(self, "_rows", [])
