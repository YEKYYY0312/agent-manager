from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent_devtools import new_run
from agent_devtools.team import TeamAccess, TeamProject, TeamTraceService, ProjectTraceStore
from agent_devtools.team_api import create_server, create_postgres_server


def test_team_api_authorizes_project_trace_reads() -> None:
    project = TeamProject.create("Support", admin_token="admin")
    writer = project.issue_token("writer", TeamAccess.WRITER)
    service = TeamTraceService("support", project, ProjectTraceStore())
    trace = new_run("ticket")
    service.upsert(writer, trace, retention_days=7)
    server = create_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/projects/support/traces/{trace.run.id}"
        request = Request(url, headers={"Authorization": f"Bearer {writer}"})
        with urlopen(request) as response:
            assert json.loads(response.read())["run"]["id"] == trace.run.id
        try:
            urlopen(url)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("missing bearer token was accepted")
    finally:
        server.shutdown(); thread.join(timeout=2); server.server_close()


def test_team_api_exposes_postgres_server_factory() -> None:
    assert callable(create_postgres_server)


def test_team_api_writes_lists_and_purges_project_traces() -> None:
    project = TeamProject.create("Support", admin_token="admin")
    writer = project.issue_token("writer", TeamAccess.WRITER)
    reader = project.issue_token("reader", TeamAccess.READER)
    service = TeamTraceService("support", project, ProjectTraceStore())
    trace = new_run("ticket search")
    server = create_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/api/projects/support"
        request = Request(
            f"{base_url}/traces",
            data=json.dumps({"trace": trace.to_dict(), "retention_days": 0}).encode("utf-8"),
            headers={"Authorization": f"Bearer {writer}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            assert json.loads(response.read()) == {"run_id": trace.run.id}

        with urlopen(Request(f"{base_url}/traces?query=ticket", headers={"Authorization": f"Bearer {reader}"})) as response:
            assert [item["run"]["id"] for item in json.loads(response.read())["traces"]] == [trace.run.id]

        request = Request(f"{base_url}/expired", headers={"Authorization": "Bearer admin"}, method="DELETE")
        with urlopen(request) as response:
            assert json.loads(response.read()) == {"deleted": 1}

        request = Request(
            f"{base_url}/traces",
            data=json.dumps({"trace": trace.to_dict(), "retention_days": 1}).encode("utf-8"),
            headers={"Authorization": f"Bearer {reader}", "Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
