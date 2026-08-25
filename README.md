# Physics Reviewer

Physics Reviewer is a Qwen and LangGraph multi-agent system for structured review of
physics papers. It accepts one or more PDFs through a web interface, processes them in
background tasks, retrieves related literature, and produces evidence-bound specialist
findings with a calibrated scorecard.

The application is designed as a review-support tool. Its output should be checked by a
qualified human reviewer and must not be treated as an autonomous academic decision.

## Features

- Web interface for single or batch PDF upload.
- Synchronous text/PDF APIs and asynchronous batch processing.
- Rule-based document signals and budget-aware specialist routing.
- Physics, novelty, citation, and reproducibility specialist checks.
- External retrieval from arXiv and Semantic Scholar.
- Temporary Chroma vector search for agent-specific RAG context.
- Six-dimension scorecard and calibrated overall score.
- CSV and XLSX batch exports.
- Persistent SQLite task history and four cache layers.
- Docker Compose deployment with Caddy TLS and Basic Auth.

## Requirements

- Python 3.11 or 3.12
- A Qwen-compatible API key
- Internet access for Qwen and optional external literature retrieval

## Local Setup

```powershell
git clone git@github.com:poooder/physics-reviewer.git
cd physics-reviewer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
QWEN_API_KEY=replace_with_your_key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
QWEN_EMBEDDING_MODEL=text-embedding-v4
QWEN_TEMPERATURE=0.2

MAX_PAPER_CHARS=120000
ROUTER_MAX_SPECIALIST_CALLS=3

LITERATURE_SEARCH_ENABLED=true
LITERATURE_SEARCH_LIMIT=5
SEMANTIC_SCHOLAR_API_KEY=
VECTOR_STORE_DIR=chroma_store
LITERATURE_MAX_DISTANCE=0.8

DATABASE_URL=sqlite:///physics_reviewer.db
TASK_WORKER_COUNT=2

CACHE_ENABLED=true
LITERATURE_CACHE_TTL_SECONDS=86400
REVIEW_CACHE_TTL_SECONDS=2592000

QWEN_REQUEST_TIMEOUT_SECONDS=120
QWEN_RETRY_ATTEMPTS=2
EMBEDDING_REQUEST_TIMEOUT_SECONDS=45
```

`SEMANTIC_SCHOLAR_API_KEY` is optional. Never commit `.env`; it is ignored by Git.

## Run

```powershell
python -m uvicorn physics_reviewer.api:app --host 0.0.0.0 --port 8011 --reload
```

Open:

- Web UI: `http://127.0.0.1:8011`
- API documentation: `http://127.0.0.1:8011/docs`
- Health check: `http://127.0.0.1:8011/health`

For local-only access, use `--host 127.0.0.1`. Use `0.0.0.0` only when access from
Docker, a trusted LAN, or Tailscale is required, and protect the host with an appropriate
firewall rule.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service health check |
| `POST` | `/reviews/text` | Synchronous review of JSON text input |
| `POST` | `/reviews/pdf` | Synchronous review of one PDF |
| `POST` | `/reviews/pdf/async` | Queue one PDF review |
| `POST` | `/batches/pdf` | Queue a batch of PDFs |
| `GET` | `/tasks/{task_id}` | Poll one task |
| `GET` | `/batches/{batch_id}` | Poll a batch |
| `GET` | `/batches/{batch_id}/export?format=csv` | Export a batch as CSV |
| `GET` | `/batches/{batch_id}/export?format=xlsx` | Export a batch as XLSX |
| `GET` | `/literature/search?q=...&limit=5` | Test external retrieval |

The frontend stores the most recent batch ID in browser local storage and restores its
results after a refresh.

## Review Workflow

```text
intake
  -> rule_extraction
  -> router
  -> literature_search
  -> physics_check
  -> citation_check
  -> novelty_check
  -> reproducibility_check
  -> rubric_scoring
  -> report_generation
```

Rule extraction and routing run locally. Specialist graph nodes that were not selected by
the router are no-ops and do not call Qwen. `ROUTER_MAX_SPECIALIST_CALLS` limits specialist
model calls, while the final rubric call always runs to produce a consistent report.

Literature retrieval runs only when enabled and when novelty or citation review is selected.
Retrieved papers are embedded into a temporary Chroma collection. Each relevant agent uses
a focused vector query; if vector retrieval fails, it falls back to the retrieved paper list.
The temporary collection is deleted after the review.

## Scoring

The report contains six integer dimensions from 1 to 5:

| Dimension | Overall weight |
| --- | ---: |
| Novelty | 12% |
| Physics correctness | 25% |
| Method rigor | 23% |
| Reproducibility | 8% |
| Citation quality | 12% |
| Writing quality | 20% |

The application calculates the weighted overall score deterministically. Qwen may adjust the
weighted value by at most five points for holistic qualities. Prompts require specific evidence
for fatal findings, treat missing or unparsed information as uncertainty, and prohibit duplicate
penalties for the same issue.

## Caching

SQLite stores four persistent cache types:

| Cache | Key | Default expiry |
| --- | --- | --- |
| PDF extraction | PDF SHA-256 and extractor version | No expiry |
| Complete review | Paper-text hash and review configuration | 30 days |
| Literature search | Normalized query and result limit | 24 hours |
| Embedding | Text hash, embedding model, and base URL | No expiry |

Concurrent requests use namespace-isolated locks to prevent duplicate work without deadlocking
nested review, retrieval, and embedding operations. Set `CACHE_ENABLED=false` for repeated-run
consistency experiments; otherwise identical submissions can return the first cached report.

To clear only cache data while preserving tasks and reports:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('physics_reviewer.db'); c.execute('DELETE FROM cache_entries'); c.commit()"
```

## CLI

```powershell
python -m physics_reviewer.cli --text sample.txt
python -m physics_reviewer.cli --pdf paper.pdf
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests cover API contracts, batch exports, graph routing, score calibration, retrieval filtering,
PDF/review/literature/embedding caches, cache expiry, and cache-lock isolation.

## Single-Server Deployment

The included Compose configuration is intended for one small trial server. It uses SQLite and
an in-process thread pool, so run exactly one `app` container.

1. Point a domain at an Ubuntu server and allow inbound TCP ports 80 and 443.
2. Install Docker Engine and the Docker Compose plugin.
3. Copy `.env.production.example` to `.env.production` and set the required values.
4. Generate a Caddy password hash:

   ```bash
   docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'choose-a-long-password'
   ```

5. Put the hash in `BASIC_AUTH_HASH`, replacing each `$` with `$$` for Compose.
6. Start the services:

   ```bash
   docker compose --env-file .env.production up -d --build
   docker compose ps
   docker compose logs -f app
   ```

Caddy obtains TLS certificates automatically and protects every route with Basic Auth. Back up
the `reviewer_data` volume before upgrades.

## Limitations and Privacy

- PDF parsing currently supports text-based PDFs only; there is no OCR pipeline.
- Equations, tables, figures, and multi-column layouts may be extracted imperfectly.
- Text beyond `MAX_PAPER_CHARS` is not reviewed and is reported as truncated.
- Retrieved literature can be incomplete or irrelevant and cannot prove absence of novelty.
- Qwen output can be inconsistent or incorrect and requires human verification.
- Paper text is transmitted to the configured Qwen endpoint. Titles/search queries are sent to
  external literature services when retrieval is enabled.
- SQLite, local caching, and the thread pool are single-instance components. Service restarts mark
  incomplete tasks as failed because uploaded PDF bytes are not persisted for recovery.
- Multi-instance deployment requires a shared database, durable object storage, and a distributed
  task queue such as Celery or RQ with Redis.

## Project Structure

```text
physics_reviewer/
  agents.py           LangGraph workflow, specialist prompts, and scoring
  api.py              FastAPI routes and static frontend hosting
  cache_store.py      Persistent SQLite cache and concurrency locks
  db.py               Task persistence
  knowledge_store.py  Chroma storage and Qwen embeddings
  literature.py       arXiv and Semantic Scholar retrieval
  pdf_parser.py       PDF text extraction and extraction cache
  tasks.py            Local asynchronous task runner
  static/             Browser interface
tests/                Automated test suite
```
