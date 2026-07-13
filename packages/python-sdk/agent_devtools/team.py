"""Project roles and hashed access tokens for shared Trace services."""

from __future__ import annotations

import hashlib
import importlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Iterable

from .trace import Trace


class TeamAccess(IntEnum):
    READER = 1
    WRITER = 2
    ADMIN = 3


@dataclass(frozen=True)
class TeamPrincipal:
    name: str
    role: TeamAccess


@dataclass
class TeamProject:
    name: str
    _tokens: dict[str, TeamPrincipal] = field(default_factory=dict, repr=False)

    @classmethod
    def create(cls, name: str, *, admin_token: str) -> "TeamProject":
        project = cls(name=name)
        project._tokens[_hash_token(admin_token)] = TeamPrincipal("admin", TeamAccess.ADMIN)
        return project

    def issue_token(self, name: str, role: TeamAccess) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[_hash_token(token)] = TeamPrincipal(name, role)
        return token

    def authorize(self, token: str, minimum_role: TeamAccess) -> TeamPrincipal:
        principal = self._tokens.get(_hash_token(token))
        if principal is None or principal.role < minimum_role:
            raise PermissionError("Project token does not have sufficient access")
        return principal

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "member_count": len(self._tokens)}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def project_trace_key(project_id: str, run_id: str) -> str:
    if not project_id or not run_id or ":" in project_id:
        raise ValueError("project_id and run_id must be non-empty; project_id cannot contain ':'")
    return f"{project_id}:{run_id}"


@dataclass
class ProjectTraceStore:
    """Project-scoped Trace boundary used by team storage adapters."""

    _traces: dict[str, Trace] = field(default_factory=dict, repr=False)

    def upsert(self, project_id: str, trace: Trace) -> str:
        key = project_trace_key(project_id, trace.run.id)
        self._traces[key] = trace
        return trace.run.id

    def get(self, project_id: str, run_id: str) -> Trace | None:
        return self._traces.get(project_trace_key(project_id, run_id))

    def list(self, project_id: str) -> Iterable[Trace]:
        prefix = f"{project_id}:"
        return [trace for key, trace in self._traces.items() if key.startswith(prefix)]


@dataclass
class TeamTraceService:
    project_id: str
    project: TeamProject
    store: ProjectTraceStore
    _expires_at: dict[str, datetime] = field(default_factory=dict, repr=False)

    def upsert(self, token: str, trace: Trace, *, retention_days: int) -> str:
        self.project.authorize(token, TeamAccess.WRITER)
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        run_id = self.store.upsert(self.project_id, trace)
        self._expires_at[project_trace_key(self.project_id, run_id)] = _utc_now() + timedelta(days=retention_days)
        return run_id

    def get(self, token: str, run_id: str) -> Trace | None:
        self.project.authorize(token, TeamAccess.READER)
        return self.store.get(self.project_id, run_id)

    def list(self, token: str, *, query: str | None = None, limit: int = 100) -> list[Trace]:
        self.project.authorize(token, TeamAccess.READER)
        return _filter_traces(self.store.list(self.project_id), query=query, limit=limit)

    def purge_expired(self, token: str) -> int:
        self.project.authorize(token, TeamAccess.ADMIN)
        now = _utc_now()
        expired = [key for key, expires_at in self._expires_at.items() if expires_at <= now]
        for key in expired:
            self._expires_at.pop(key, None)
            self.store._traces.pop(key, None)
        return len(expired)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresTeamRepository:
    """PostgreSQL schema and connection entry point for shared team data."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("database_url is required")
        self.database_url = database_url
        self._psycopg = importlib.import_module("psycopg")
        self._init_db()

    @staticmethod
    def schema_sql() -> str:
        return """
        CREATE TABLE IF NOT EXISTS team_projects (
          project_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS team_tokens (
          project_id TEXT NOT NULL REFERENCES team_projects(project_id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL,
          member_name TEXT NOT NULL,
          role INTEGER NOT NULL CHECK (role BETWEEN 1 AND 3),
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (project_id, token_hash)
        );
        CREATE TABLE IF NOT EXISTS team_traces (
          project_id TEXT NOT NULL REFERENCES team_projects(project_id) ON DELETE CASCADE,
          run_id TEXT NOT NULL,
          trace_json JSONB NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (project_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_team_traces_expires_at ON team_traces(expires_at);
        """

    def _init_db(self) -> None:
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(self.schema_sql())

    def create_project(self, project_id: str, name: str, *, admin_token: str) -> None:
        if not project_id or not name:
            raise ValueError("project_id and name are required")
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO team_projects (project_id, name) VALUES (%s, %s)", (project_id, name))
                cursor.execute(
                    "INSERT INTO team_tokens (project_id, token_hash, member_name, role) VALUES (%s, %s, %s, %s)",
                    (project_id, _hash_token(admin_token), "admin", int(TeamAccess.ADMIN)),
                )

    def issue_token(self, project_id: str, admin_token: str, name: str, role: TeamAccess) -> str:
        self.authorize(project_id, admin_token, TeamAccess.ADMIN)
        token = secrets.token_urlsafe(32)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO team_tokens (project_id, token_hash, member_name, role) VALUES (%s, %s, %s, %s)",
                    (project_id, _hash_token(token), name, int(role)),
                )
        return token

    def authorize(self, project_id: str, token: str, minimum_role: TeamAccess) -> TeamPrincipal:
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT member_name, role FROM team_tokens WHERE project_id = %s AND token_hash = %s",
                    (project_id, _hash_token(token)),
                )
                row = cursor.fetchone()
        if row is None or TeamAccess(int(row[1])) < minimum_role:
            raise PermissionError("Project token does not have sufficient access")
        return TeamPrincipal(str(row[0]), TeamAccess(int(row[1])))

    def upsert_trace(self, project_id: str, token: str, trace: Trace, *, retention_days: int) -> str:
        self.authorize(project_id, token, TeamAccess.WRITER)
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        expires_at = _utc_now() + timedelta(days=retention_days)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO team_traces (project_id, run_id, trace_json, expires_at)
                    VALUES (%s, %s, %s::jsonb, %s)
                    ON CONFLICT (project_id, run_id) DO UPDATE SET trace_json = EXCLUDED.trace_json, expires_at = EXCLUDED.expires_at""",
                    (project_id, trace.run.id, json.dumps(trace.to_dict(), ensure_ascii=False), expires_at),
                )
        return trace.run.id

    def get_trace(self, project_id: str, token: str, run_id: str) -> Trace | None:
        self.authorize(project_id, token, TeamAccess.READER)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT trace_json FROM team_traces WHERE project_id = %s AND run_id = %s AND expires_at > NOW()",
                    (project_id, run_id),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        value = row[0]
        return Trace.from_dict(json.loads(value) if isinstance(value, str) else value)

    def list_traces(self, project_id: str, token: str, *, query: str | None = None, limit: int = 100) -> list[Trace]:
        self.authorize(project_id, token, TeamAccess.READER)
        safe_limit = _validate_limit(limit)
        clauses = ["project_id = %s", "expires_at > NOW()"]
        params: list[object] = [project_id]
        if query:
            clauses.append("trace_json->'run'->>'task' ILIKE %s")
            params.append(f"%{query}%")
        params.append(safe_limit)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT trace_json FROM team_traces WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT %s",
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [Trace.from_dict(json.loads(row[0]) if isinstance(row[0], str) else row[0]) for row in rows]

    def purge_expired(self, project_id: str, admin_token: str) -> int:
        self.authorize(project_id, admin_token, TeamAccess.ADMIN)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM team_traces WHERE project_id = %s AND expires_at <= NOW()", (project_id,))
                return int(cursor.rowcount)


def _filter_traces(traces: Iterable[Trace], *, query: str | None, limit: int) -> list[Trace]:
    safe_limit = _validate_limit(limit)
    needle = query.casefold() if query else ""
    return [trace for trace in traces if not needle or needle in trace.run.task.casefold()][:safe_limit]


def _validate_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    return limit
