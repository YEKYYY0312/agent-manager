# Team Trace Service

The team service is self-hosted. It uses PostgreSQL for projects, hashed bearer
tokens, and retained Trace JSON. Install the optional dependency first:

```powershell
py -m pip install -e ".[postgres]"
```

Set `AGENT_DEVTOOLS_DATABASE_URL` from a secret manager, then start the
loopback service with `agent-devtools team-serve`. The repository creates its
own tables and indexes at startup. Tokens are stored only as SHA-256 hashes.

Routes require `Authorization: Bearer <token>`:

- `POST /api/projects/{project}/traces` requires Writer.
- `GET /api/projects/{project}/traces` and `GET .../traces/{run}` require Reader.
- `DELETE /api/projects/{project}/expired` requires Admin.

Write requests contain `{ "trace": <Trace JSON>, "retention_days": 30 }`.
Read lists accept `query` and `limit` (1-100). Expired records are excluded
from reads and deleted through the Admin route. Put TLS, a reverse proxy, and
network access control in front of this loopback service for team deployment.

## Offline Evaluation

Run deterministic dataset checks without sending data to a model:

```powershell
agent-devtools evaluate eval\dataset.json answers.json --output evaluation-report.json
agent-devtools annotate annotations.jsonl case-1 reviewer --score accuracy=0.8 --score completeness=0.7
agent-devtools evaluate eval\dataset.json answers.json --annotations annotations.jsonl
```

The report includes per-case deterministic coverage and safety scores, optional
human quality scores, difficulty strata, and failure clusters. A nonzero exit
code makes `evaluate` suitable for CI quality gates.
