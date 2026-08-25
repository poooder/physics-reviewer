# Physics Reviewer

Minimal Qwen + LangGraph multi-agent prototype for batch physics paper review.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env`:

```env
QWEN_API_KEY=your_key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

For a local OpenAI-compatible Qwen server, point `QWEN_BASE_URL` at that server and set `QWEN_API_KEY` to any accepted value.

## Run API

```powershell
uvicorn physics_reviewer.api:app --reload
```

Open:

- `POST /reviews/text` with JSON body `{ "title": "...", "paper_text": "..." }`
- `POST /reviews/pdf` with multipart file field `file` for synchronous review
- `POST /reviews/pdf/async` with multipart file field `file` for a background task
- `POST /batches/pdf` with multipart file field `files` for batch review
- `GET /tasks/{task_id}` to poll one task
- `GET /batches/{batch_id}` to poll a batch
- `GET /literature/search?q=...&limit=5` to debug external retrieval

The local background backend uses a small thread pool and SQLite:

```env
DATABASE_URL=sqlite:///physics_reviewer.db
TASK_WORKER_COUNT=2
ROUTER_MAX_SPECIALIST_CALLS=3
MAX_PAPER_CHARS=120000
CACHE_ENABLED=true
LITERATURE_CACHE_TTL_SECONDS=86400
REVIEW_CACHE_TTL_SECONDS=2592000
QWEN_REQUEST_TIMEOUT_SECONDS=120
QWEN_RETRY_ATTEMPTS=2
EMBEDDING_REQUEST_TIMEOUT_SECONDS=45
```

This is enough for local development. For production-scale batch review, replace the task backend with Celery/RQ and keep the same public API shape.

## Run CLI

```powershell
python -m physics_reviewer.cli --text sample.txt
python -m physics_reviewer.cli --pdf paper.pdf
```

## Agent Flow

```text
intake
  -> rule_extraction (local, no Qwen call)
  -> router (local, specialist call budget)
  -> literature_search
  -> selected specialist checks
  -> rubric_scoring
  -> report_generation
```

`ROUTER_MAX_SPECIALIST_CALLS=3` controls the maximum number of specialist Qwen calls per paper. The final rubric call still runs so every report has a consistent scorecard.

`MAX_PAPER_CHARS=120000` is the default full-text limit. A longer paper is explicitly marked as partially reviewed instead of treating unseen content as a verified weakness.

The SQLite cache stores PDF extraction results, complete reviews keyed by paper-content hash,
literature searches, and Qwen embeddings. PDF and embedding entries are automatically isolated by
extractor/model version. Literature results expire after one day and complete reviews after 30 days
by default. Set `CACHE_ENABLED=false` to bypass every cache while running controlled evaluations.

Qwen and embedding requests have explicit timeouts and bounded retries so an upstream request
cannot occupy a worker indefinitely. Tasks left queued or running by a service restart are marked
failed on startup because the local thread-pool backend does not persist uploaded PDF bytes.

Batch results can be downloaded after upload:

```text
GET /batches/{batch_id}/export?format=csv
GET /batches/{batch_id}/export?format=xlsx
```

## Single-server deployment

The included Docker Compose deployment is for one small trial server. It uses SQLite and the
local background worker, so run exactly one `app` container. It is not a multi-instance setup.

1. Rent an Ubuntu 22.04+ server with at least 2 CPU cores and 4 GB RAM, create a DNS `A` record
   from your domain to the server public IP, and allow inbound TCP ports 80 and 443 only.
2. Install Docker Engine and the Docker Compose plugin on the server, then upload this project.
3. Copy the deployment template and set its secrets:

   ```bash
   cp .env.production.example .env.production
   docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'choose-a-long-password'
   ```

   Put the generated bcrypt hash in `BASIC_AUTH_HASH`. Replace each `$` in the hash with `$$`.
   Set `DOMAIN` and `QWEN_API_KEY`; never commit `.env.production`.
4. Start and inspect the service:

   ```bash
   docker compose --env-file .env.production up -d --build
   docker compose ps
   docker compose logs -f app
   ```

Open `https://your-domain`. Caddy obtains and renews the TLS certificate automatically, and
every route is protected by the configured HTTP Basic Auth account.

Back up the `reviewer_data` Docker volume before upgrades. When the trial grows beyond one
server, replace SQLite/local workers with PostgreSQL and Redis/Celery or RQ before scaling.
