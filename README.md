# Argus — Find Jobs That You Want

An AI-assisted job intelligence pipeline that continuously finds roles, filters noise, scores fit, learns from your feedback, and delivers only the strongest matches to Telegram + a web dashboard.

---

## TL;DR

Argus currently does this end-to-end:

1. Fetches jobs from multiple sources (JobSpy, RemoteOK, optional Adzuna)
2. Applies deterministic pre-filtering (cheap rejection layer)
3. Scores survivors with an LLM (Gemini via OpenRouter)
4. Re-ranks with feedback-aware preference signals (TF-IDF similarity)
5. Sends top jobs to Telegram with 👍 / 👎 inline actions
6. Tracks approved jobs in a dashboard (`saved`, `applied`, `interviewing`, `offered`, `rejected`)

It is already useful as a personal AI job scout and tracker, and it has a strong base for productionization.

---

## What This Project Is (and Is Not)

### ✅ It **is**
- A practical AI job discovery and triage system for individual use
- A hybrid scoring pipeline (rules + LLM + learned preference signal)
- A full loop from discovery → feedback → incremental personalization
- A deployable FastAPI app with Telegram integration and dashboard UI

### ❌ It is **not yet**
- A multi-tenant SaaS platform
- A hardened enterprise backend with auth, RBAC, and rate limiting
- A system with full test coverage and CI quality gates

---

## Architecture (Current)

### Core runtime components
- `agent.py` — Orchestrates the pipeline run
- `server.py` — FastAPI API + dashboard hosting + Telegram webhook receiver
- `scraper/` — Source adapters (`job_scraper.py`, `remoteok.py`, `adzuna.py`)
- `scoring/` — Deterministic prefilter + LLM scoring
- `learning/` — Feedback-based preference engine
- `telegram/bot.py` — Delivery + callback processing
- `db/` — SQLAlchemy models, sessions, and CRUD logic
- `dashboard/` — Static frontend (HTML/CSS/JS)

### Data flow
`Sources → PreFilter → LLM Score → Hybrid Re-rank → jobs_temp → Telegram feedback/dashboard actions → jobs_main + feedback learning`

### Storage model
- `jobs_temp`: staged candidates awaiting review
- `jobs_main`: accepted jobs and tracking lifecycle
- `feedback`: thumbs up/down training signal
- `filter_config`: DB-backed runtime filters
- `agent_state`: persisted offsets/state (e.g., Telegram update offset)

---

## Features

- Multi-source scraping with deduplication
- Rule-based filtering for speed + cost control
- LLM structured JSON scoring with experience-aware constraints
- Feedback learning:
  - TF-IDF + cosine similarity active after minimal feedback
  - Logistic Regression path defined for larger feedback sets
- Telegram:
  - Rich formatted job cards
  - Inline 👍 Save / 👎 Pass
  - Webhook-first with polling fallback
- Dashboard:
  - Saved and pending review tabs
  - Status updates and notes
  - Settings editor for live filter changes
  - Learning progress visibility
  - Manual trigger for agent run

---

## Tech Stack

- Python 3.11
- FastAPI + Uvicorn
- SQLAlchemy (SQLite local / PostgreSQL production)
- OpenRouter (OpenAI-compatible client) for LLM scoring
- scikit-learn + NumPy for preference learning
- Telegram Bot API
- GitHub Actions for scheduled runs

---

## Quick Start (Local)

### 1) Clone & install
```bash
pip install -r requirements.txt
```

### 2) Configure environment
```bash
cp .env.example .env
```

Set required values:
- `OPENROUTER_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ADMIN_USER_ID` (recommended)
- `DATABASE_URL` (default SQLite is fine for local)

### 3) Run server (dashboard + API + webhook endpoint)
```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### 4) Run one agent cycle manually
```bash
python agent.py
```

Open dashboard at:
- `http://localhost:8000/`

---

## Configuration

Main config lives in:
- `.env` (runtime secrets/overrides)
- `config.py` (defaults and constants)

Important knobs:
- `JOBS_PER_SOURCE`
- `TOP_K_DELIVER`
- `DATABASE_URL`
- `OPENROUTER_MODEL`
- `WEBHOOK_BASE_URL`
- `KEEP_ALIVE_INTERVAL`

Filters (roles, locations, skills, companies, salary limits, active sources) are DB-driven and editable from dashboard settings.

---

## API Surface (Summary)

Main endpoints:
- `GET /api/stats`
- `GET /api/jobs`
- `GET /api/jobs/{hash}`
- `PUT /api/jobs/{hash}`
- `DELETE /api/jobs/{hash}`
- `GET /api/pending`
- `POST /api/jobs/{hash}/approve`
- `POST /api/jobs/{hash}/reject`
- `GET /api/filters`
- `PUT /api/filters/{key}`
- `GET /api/learning`
- `POST /api/trigger`
- `POST /api/telegram/webhook`
- `POST /api/telegram/setup-webhook`
- `GET /api/telegram/webhook-info`
- `GET /health`

---

## Deployment

### Current supported path
- **Render** for FastAPI hosting (`Procfile`)
- **GitHub Actions** scheduled workflow (`.github/workflows/job_agent.yml`) for hourly agent runs

### Current workflow
- Server stays live for dashboard/webhook
- Agent can run from:
  - GitHub Actions schedule
  - Dashboard trigger endpoint
  - Direct CLI invocation

---

## Truthful Current Gaps / Issues to Address

This section is intentionally candid.

1. **No authentication on dashboard APIs**  
   Job update/delete/filter-change/trigger endpoints are publicly callable if URL is exposed.

2. **CORS is fully open (`*`)**  
   Current CORS config is permissive and not ideal for production.

3. **Default webhook secret is weak/static**  
   `WEBHOOK_SECRET` has a hardcoded default and should be mandatory random secret in production.

4. **No request rate limiting or abuse protection**  
   Trigger/webhook/API endpoints can be spammed.

5. **No formal test suite and quality gates**  
   There are no repository tests/lint configs currently defined.

6. **`apscheduler` dependency exists but is not wired in runtime**  
   Scheduling is currently done via GitHub Actions + manual trigger.

7. **Single-user assumptions**  
   `TELEGRAM_CHAT_ID`, `ADMIN_USER_ID`, and filters are globally scoped.

8. **Dedup hash strategy may over-merge**  
   Hash uses normalized title + company; distinct roles at same company with same title may collapse.

---

## How to Make This Best-in-Class (Production-Grade Roadmap)

### 1) Security and access control (highest priority)
- Add auth (JWT/session) for dashboard and all mutating APIs
- Add role-based control (admin/user)
- Enforce strict CORS allowlist by environment
- Make `WEBHOOK_SECRET` required (no default)
- Add API rate limiting and basic WAF protections
- Add secrets management (Render/Actions/1Password/Vault), not `.env` for prod

### 2) Reliability and job orchestration
- Move long-running agent runs to worker queue (Celery/RQ/Arq/Temporal)
- Add distributed locking to prevent overlapping runs
- Use idempotency keys for run stages
- Add dead-letter handling and retry policies per source/scoring step

### 3) Data architecture and scale
- Standardize on PostgreSQL in production
- Add migrations (Alembic) and schema versioning
- Add retention policies for `jobs_temp` and stale feedback artifacts
- Introduce source-level provenance and canonical job IDs for better dedup quality

### 4) Quality and model performance
- Add automated tests:
  - unit tests for prefilter/feature extraction
  - integration tests for pipeline stages
  - API tests for critical endpoints
- Add offline evaluation harness:
  - precision@k on historical feedback
  - false-positive/false-negative audits
- Version prompts and scoring configs explicitly
- Add model fallback chain + budget-aware scoring

### 5) Observability and operations
- Structured logging (JSON) with correlation IDs
- Metrics (Prometheus/OpenTelemetry): source yield, pass rate, LLM latency/cost, approval rate
- Alerting on failure rates, empty runs, webhook breakage, and quota exhaustion
- Run-level audit record with traceable stage timing

### 6) Product differentiation hacks (high leverage)
- “Why this job now” explanation linked to past likes
- Auto-tailored resume bullets per job (human-approved)
- Smart application tracker with reminders and SLA nudges
- Interview kit generation based on JD and user profile
- Duplicate-posting intelligence across sources
- Weekly market insights: skill demand drift, salary ranges, role momentum

### 7) Multi-tenant SaaS evolution
- Tenant-scoped data model and auth boundaries
- Per-tenant source configs, models, and cost controls
- Billing-aware usage metering (LLM calls, fetched jobs, alerts)
- Background worker pool partitioned by tenant priority

---

## Practical Next Milestones

If you want fastest real-world improvement, do these first:

1. Add auth + lock down CORS
2. Add API rate limiting + secure webhook secret policy
3. Add Alembic migrations + Postgres-only production profile
4. Add baseline tests for prefilter and API endpoints
5. Add metrics + alerts around pipeline health

---

## License

No license file is currently present in the repository.  
Add one before public/commercial distribution.

