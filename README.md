<div align="center">

# 📬 Gmail AI Auto Labeler

**Intelligent email classification powered by Groq AI — zero-cost, fully automated.**

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Groq](https://img.shields.io/badge/Groq-FF6600?style=for-the-badge&logo=groq&logoColor=white)](https://groq.com/)
[![Gmail](https://img.shields.io/badge/Gmail%20API-EA4335?style=for-the-badge&logo=gmail&logoColor=white)](https://developers.google.com/gmail/api)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com/)
[![Vercel](https://img.shields.io/badge/Vercel-000000?style=for-the-badge&logo=vercel&logoColor=white)](https://vercel.com/)
[![Render](https://img.shields.io/badge/Render-46E3B7?style=for-the-badge&logo=render&logoColor=white)](https://render.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge&logo=mit&logoColor=white)](LICENSE)
[![CI/CD](https://img.shields.io/github/actions/workflow/status/yashshinde0080/Gmail-labeler-AI/docker.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white)](https://github.com/yashshinde0080/Gmail-labeler-AI/actions)
[![Code Coverage](https://img.shields.io/codecov/c/github/yashshinde0080/Gmail-labeler-AI?style=for-the-badge&logo=codecov&logoColor=white)](https://codecov.io/gh/yashshinde0080/Gmail-labeler-AI)

---

</div>

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🤖 **AI Classification** | Uses **Groq's Llama 3.1 8B Instant** (free tier) to classify every incoming email into smart categories. |
| 🏷️ **Auto-Labeling** | Creates and applies color-coded Gmail labels automatically — no manual sorting needed. |
| ⭐ **Smart Prioritization** | *Configurable — AI detects importance but starring is disabled by default.* |
| 🗂️ **Auto-Archive Clutter** | *Configurable — AI detects low-importance but archiving is disabled by default.* |
| 🔍 **Fuzzy Label Matching** | Uses `thefuzz` to match AI-suggested labels with existing Gmail labels — no duplicates. |
| 🔐 **Encrypted Token Storage** | OAuth2 tokens encrypted at rest using `cryptography.fernet`. |
| 📊 **Rich Metrics Dashboard** | `/stats`, `/metrics`, and `/logs` endpoints provide full visibility. |
| 🔔 **Push Notifications** | Optional Gmail Pub/Sub push notifications for near-instant processing. |
| 🔄 **Auto-Retry** | Exponential backoff retry mechanism for failed classifications. |
| 🐳 **Docker-Ready** | Multi-stage `Dockerfile` + `docker-compose.yml` for one-command deployment. |
| ☁️ **Deploy Anywhere** | Works on **Render**, **Railway**, **Vercel** (serverless), or any VPS with Docker. |
| 🎨 **Color-Coded Labels** | 20+ predefined categories, each with a unique Gmail label color for visual scanning. |

---

## 🏗️ Architecture

```
                       ┌──────────────────────┐
                       │     Gmail Inbox      │
                       └────┬─────────────────┘
                            │ Polling (10s) / Push (Pub/Sub)
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Scheduler (APScheduler)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Poll New     │  │ Retry Failed │  │ Renew Gmail Watch      │  │
│  │ Emails (10s) │  │ (15 min)     │  │ (24h)                  │  │
│  └──────┬───────┘  └──────┬───────┘  └────────────────────────┘  │
└─────────┼─────────────────┼──────────────────────────────────────┘
          │                 │
          ▼                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Processing Pipeline                           │
│                                                                  │
│  1. Fetch Message ───► 2. Clean Text ───► 3. AI Classification   │
│        ▲                                        │                │
│        │                                        ▼                │
│  6. Log to DB ◄─── 5. Apply Actions ◄─── 4. Resolve Label        │
│                       (Label/Star/Archive)                       │
└──────────────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────┐     ┌──────────────────────┐
│   PostgreSQL /       │     │   Gmail API          │
│   SQLite (dev)       │     │   (labels/actions)   │
└──────────────────────┘     └──────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- A [Groq](https://console.groq.com) account (free tier)
- A [Google Cloud Project](https://console.cloud.google.com) with Gmail API enabled

### 1. Clone & Install

```bash
git clone https://github.com/yashshinde0080/Gmail-labeler-AI.git
cd gmail-ai-labeler

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Fill in your credentials (see [Credentials Setup](#credentials-setup) below):

```env
# Required
GROQ_API_KEY=gsk_your_groq_api_key
GMAIL_CLIENT_ID=your_client_id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your_client_secret

# Required for token encryption
FERNET_KEY=your_base64_32_byte_key

# Optional — defaults work out of the box
DATABASE_URL=sqlite:///./data/gmail.db
```

Generate a Fernet key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Run

```bash
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger API docs.

### 4. Authenticate with Gmail

Visit **http://localhost:8000/login** to authorize the app with your Google account.

---

## 🔑 Credentials Setup

### 🤖 Groq API Key

| Step | Action |
|------|--------|
| 1 | Create an account at [console.groq.com](https://console.groq.com) |
| 2 | Navigate to **API Keys** → **Create API Key** |
| 3 | Copy the key into `GROQ_API_KEY` in your `.env` |

The free tier offers generous limits (**30 requests/min**) with Llama 3.1 8B Instant — more than enough for personal inbox management.

### 📧 Gmail OAuth2

#### Step 1 — Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g., `gmail-ai-labeler`)
3. **APIs & Services** → **Enable APIs** → search for **Gmail API** → **Enable**

#### Step 2 — OAuth Consent Screen

1. **APIs & Services** → **OAuth consent screen**
2. User type: **External** (or Internal if using Google Workspace)
3. Fill in:
   - App name: `Gmail AI Labeler`
   - Support email: your email
   - Developer contact: your email
4. **Add scope**: `https://www.googleapis.com/auth/gmail.modify`
5. Add your Gmail address as a **Test user**

#### Step 3 — OAuth 2.0 Credentials

1. **APIs & Services** → **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
2. Application type: **Desktop app**
3. Save the generated `Client ID` and `Client Secret`
4. Add them to your `.env` as `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET`

---

## ⚙️ Configuration Reference

All configuration is managed through environment variables or a `.env` file, validated by `pydantic-settings`.

### AI & Groq

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | **Required.** Your Groq API key. |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Groq model for classification. |
| `GROQ_TIMEOUT` | `30` | API timeout in seconds. |
| `GROQ_MAX_RETRIES` | `3` | Max retries for Groq API calls. |
| `GROQ_REQUESTS_PER_MINUTE` | `30` | Proactive pacing of Groq calls (matches the 8B free tier). |
| `GROQ_RATE_LIMIT_MAX_WAIT` | `60` | Max seconds to wait for a rate-limit window before deferring a message. |

#### 🚦 Free-tier rate-limit handling

The `llama-3.1-8b-instant` free tier allows roughly **30 requests/min**, and
each classification costs prompt tokens too. The pipeline degrades gracefully
when that quota is exhausted:

1. **Paced requests** — calls are spaced by `GROQ_REQUESTS_PER_MINUTE` so a burst
   of new mail cannot blow straight through the limit.
2. **Honours `Retry-After`** — a 429 is retried using the window Groq reports,
   capped at `GROQ_RATE_LIMIT_MAX_WAIT`.
3. **Cooldown breaker** — after a 429, further calls fail fast (no wasted quota)
   until the window resets.
4. **Nothing is mislabelled** — a rate-limited email is *never* labelled
   `Uncategorised`. The message is deferred to the retry queue, and the rest of
   the batch is queued too, because the Gmail history ID has already advanced
   past it.
5. **Deferrals are free** — unlike a genuine failure, deferrals do not consume a
   message's retry budget, so a long quota window never abandons an email.

### Gmail

| Variable | Default | Description |
|----------|---------|-------------|
| `GMAIL_CLIENT_ID` | — | **Required.** Google OAuth2 client ID. |
| `GMAIL_CLIENT_SECRET` | — | **Required.** Google OAuth2 client secret. |
| `GMAIL_REDIRECT_URI` | `http://localhost:8000` | OAuth2 redirect URI. |
| `GMAIL_SCOPES` | `https://www.googleapis.com/auth/gmail.modify` | Gmail API scopes. |
| `GCP_PUBSUB_TOPIC` | — | Pub/Sub topic for push notifications. |

### Email Processing

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL_SECONDS` | `10` | How often to check for new emails (min: 10, max: 3600). |
| `CONFIDENCE_THRESHOLD` | `70` | Minimum AI confidence (0-100) to apply actions. |
| `ARCHIVE_LOW_IMPORTANCE` | `true` | Auto-archive newsletters / promotions. |
| `STAR_HIGH_IMPORTANCE` | `true` | Star urgent / high-importance emails. |
| `ENABLE_NESTED_LABELS` | `false` | Allow `Work/AI` style nested labels. |

### Database & Security

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://...` | Database connection string. Supports SQLite / PostgreSQL. |
| `FERNET_KEY` | — | **Required.** 32-byte base64 key for encrypting OAuth tokens. |
| `VERCEL_CRON_SECRET` | — | Secret to secure Vercel Cron endpoints. |

### Scheduler

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULER_ENABLED` | `true` | Set to `false` to disable background polling (e.g., on Vercel). |
| `RETRY_MAX_ATTEMPTS` | `3` | Max retries for failed messages. |
| `RETRY_BACKOFF_BASE` | `2` | Exponential backoff multiplier (minutes). |

---

## 📡 API Reference

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/` | Service info & OAuth callback handler | — |
| `GET` | `/login` | Redirect to Google OAuth2 consent screen | — |
| `GET` | `/health` | Liveness probe (used by Docker / Render) | — |
| `GET` | `/stats` | Email processing statistics | — |
| `GET` | `/labels` | All known Gmail labels | — |
| `GET` | `/logs` | Recent processed emails (paginated) | — |
| `GET` | `/metrics` | AI token usage & latency metrics | — |
| `GET` | `/watch/status` | Start / check Gmail Push Notification watch | — |
| `GET` | `/gmail/status` | Current sync state & history ID | — |
| `POST` | `/sync` | Manually trigger a processing cycle | — |
| `POST` | `/reprocess` | Re-classify a specific message | — |
| `POST` | `/webhook/gmail` | Receive Gmail Pub/Sub push notifications | — |
| `GET`/`POST` | `/api/cron/process` | Vercel Cron: process new emails | Bearer |
| `GET` | `/api/cron/retry` | Vercel Cron: retry failed emails | Bearer |
| `GET` | `/api/cron/renew_watch` | Vercel Cron: renew Gmail watch (daily) | Bearer |

> **Full interactive API docs** available at `/docs` (Swagger) and `/redoc` (ReDoc).

### Sample Responses

#### `GET /health`
```json
{
  "status": "healthy",
  "uptime_seconds": 84321,
  "timestamp": "2026-10-07T12:00:00+00:00"
}
```

#### `GET /stats`
```json
{
  "total": 245,
  "success": 240,
  "failed": 5,
  "archived": 180,
  "starred": 42,
  "avg_confidence": 87.3,
  "top_categories": [
    {"category": "📰 Newsletters", "count": 89},
    {"category": "💰 Finance", "count": 34},
    {"category": "💼 Work", "count": 28}
  ]
}
```

#### `GET /metrics`
```json
{
  "total_calls": 245,
  "success_calls": 242,
  "failed_calls": 3,
  "total_tokens_used": 84750,
  "avg_response_time_ms": 612.4
}
```

---

## 🏷️ Predefined Categories

The AI chooses from **20 curated categories**, each with a distinct Gmail label color:

| # | Category | Color |
|---|----------|-------|
| 1 | 🔴 **Urgent** | 🔴 Red |
| 2 | 🟠 **Action Required** | 🟠 Orange |
| 3 | 🟡 **Follow Up** | 🟡 Yellow |
| 4 | 🔵 **Important** | 🔵 Blue |
| 5 | 💼 **Work** | 🔵 Blue |
| 6 | 👤 **Personal** | 🟦 Light Blue |
| 7 | 💰 **Finance** | 🟢 Green |
| 8 | 🛒 **Shopping** | 🟣 Purple |
| 9 | ✈️ **Travel** | 🟢 Teal |
| 10 | 🏥 **Health** | 🔴 Red |
| 11 | 🤖 **AI & Tech** | 🟣 Purple |
| 12 | 🔐 **Security** | 🔴 Dark Red |
| 13 | 📰 **Newsletters** | ⚪ Gray |
| 14 | 🎉 **Promotions** | 🟡 Yellow |
| 15 | 📦 **Orders** | 🟢 Green |
| 16 | 📅 **Meetings** | 🟢 Teal |
| 17 | 👥 **Clients** | 🟣 Dark Purple |
| 18 | 🔄 **Waiting Reply** | 🟣 Purple |
| 19 | 📚 **Learning** | 🟦 Light Blue |
| 20 | 📂 **Archive** | ⚪ Gray |

The AI can also **dynamically propose new labels** with emoji prefixes when an email doesn't fit existing categories.

---

## ☁️ Deployment

### 🐳 Docker (Recommended)

```bash
# Build and run with Docker Compose
docker compose up --build -d
docker compose logs -f

# Or build manually
docker build -t gmail-ai-labeler .
docker run -d --env-file .env -p 8000:8000 gmail-ai-labeler
```

### ☁️ Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

1. Push code to GitHub
2. Go to [render.com](https://render.com) → **New** → **Blueprint**
3. Connect your repository
4. Set environment variables in the Render dashboard:
   - `GROQ_API_KEY`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `FERNET_KEY`
5. Click **Deploy**
6. Verify: `https://your-service.onrender.com/health`

### ▲ Vercel (Serverless)

> **Note:** Vercel does not support background threads. The scheduler is disabled by default on Vercel.

1. Set `SCHEDULER_ENABLED=false` in environment
2. Use a PostgreSQL database (e.g., [Neon](https://neon.tech) or [Supabase](https://supabase.com))
3. Set up [Vercel Cron Jobs](https://vercel.com/docs/cron-jobs) to call:
   - `GET /api/cron/process` — every 1 minute
   - `GET /api/cron/retry` — every 15 minutes
   - `GET /api/cron/renew_watch` — daily at midnight

```bash
# Deploy to Vercel
npx vercel --prod

# Set environment variables in Vercel dashboard
```

### 🚂 Railway

```bash
# Railway auto-detects Dockerfile
railway up
```

---

## 🧪 Testing

```bash
# Run all tests with coverage
pytest tests/ -v --cov=app --cov-report=term-missing

# Run specific test file
pytest tests/test_classifier.py -v

# Run with HTML coverage report
pytest tests/ --cov=app --cov-report=html
open htmlcov/index.html
```

### Test Structure

| Test File | What It Covers |
|-----------|----------------|
| `tests/test_classifier.py` | AI response parsing, validation, sanitization, and fallback logic |
| `tests/test_labels.py` | Fuzzy label matching, normalization, edge cases |
| `tests/test_api.py` | All REST endpoints — status codes, response shapes, pagination |

Tests use **mocked Gmail and Groq APIs** — no external credentials needed.

---

## 🔄 CI/CD Pipeline

The project includes a comprehensive CI/CD pipeline via GitHub Actions (`.github/workflows/docker.yml`):

| Stage | What Happens |
|-------|-------------|
| ✅ **Lint** | `ruff check` — code quality enforcement |
| ✅ **Test** | `pytest` with coverage — uploads to Codecov |
| ✅ **Docker Build** | Builds image to verify Dockerfile compiles |
| 🚀 **Deploy** | Triggers Render deploy hook on main branch pushes |

---

## 📁 Project Structure

```
gmail-ai-labeler/
├── api/
│   └── index.py                  # Vercel serverless entry point
├── app/
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── classifier.py         # Classification orchestrator
│   │   ├── groq_client.py        # Groq SDK wrapper with retry
│   │   └── prompt.py             # Prompt templates & category list
│   ├── database/
│   │   ├── __init__.py
│   │   ├── crud.py               # Database CRUD helpers
│   │   ├── db.py                 # Engine, session factory, init_db
│   │   └── models.py             # SQLAlchemy ORM models (6 tables)
│   ├── gmail/
│   │   ├── __init__.py
│   │   ├── auth.py               # OAuth2 credentials management
│   │   ├── labels.py             # Label CRUD + fuzzy matching
│   │   ├── messages.py           # Message fetch + parse + clean
│   │   └── service.py            # Gmail API service builder
│   ├── __init__.py
│   ├── api.py                    # FastAPI routes (15 endpoints)
│   ├── config.py                 # Settings via pydantic-settings
│   ├── logger.py                 # Structured logging (structlog)
│   ├── main.py                   # Application entry point
│   └── scheduler.py              # APScheduler jobs (3 recurring)
├── data/
│   ├── gmail.db                  # SQLite database (auto-created)
│   └── logs/                     # JSON log files
├── tests/
│   ├── __init__.py
│   ├── test_api.py               # API endpoint tests
│   ├── test_classifier.py        # AI classifier unit tests
│   └── test_labels.py            # Label matching tests
├── .github/workflows/
│   ├── ci.yml                    # CI pipeline
│   └── docker.yml                # Full CI/CD pipeline
├── .env.example                  # Environment template
├── .gitignore
├── .python-version               # Python 3.12
├── Dockerfile                    # Multi-stage Docker build
├── docker-compose.yml            # Local Docker setup
├── main.py                       # CLI entry point
├── README.md                     # You are here 📖
├── requirements.txt              # Python dependencies
├── ruff.toml                     # Ruff linter config
├── vercel.json                   # Vercel deployment config
└── VERCEL_DEPLOYMENT.md          # Vercel-specific guide
```

### Database Schema

Six SQLAlchemy models power the application:

| Table | Purpose |
|-------|---------|
| `processed_emails` | One row per email processed by the system |
| `labels` | Cached Gmail labels (name ↔ ID mapping) |
| `ai_logs` | Token usage & latency for every Groq call |
| `retries` | Failed messages with exponential backoff tracking |
| `app_settings` | Key-value store for runtime configuration |
| `oauth_tokens` | Encrypted Gmail OAuth2 tokens |

---

## 💰 Cost Breakdown

| Component | Estimated Cost |
|-----------|---------------|
| 🤖 **Groq API** | **Free** — 30 req/min with Llama 3.1 8B Instant |
| 📧 **Gmail API** | **Free** — standard quota (1 billion queries/day) |
| 🗄️ **SQLite / PostgreSQL** | **Free** — SQLite (local) or Neon free tier (cloud) |
| 🐳 **Docker / Render** | **Free** — Render free plan (750 hours/month) |
| ▲ **Vercel** | **Free** — hobby plan with cron jobs |
| 🏗️ **GitHub Actions** | **Free** — 2000 min/month |
| **Total** | **₹0 / $0** 💸 |

---

## 🧩 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Runtime** | Python 3.12 |
| **Web Framework** | FastAPI + Uvicorn |
| **AI / LLM** | Groq (Llama 3.1 8B Instant) |
| **Email API** | Google Gmail API v1 |
| **Database** | SQLAlchemy ORM (SQLite / PostgreSQL) |
| **Scheduler** | APScheduler |
| **OAuth** | Google OAuth2 + `google-auth-oauthlib` |
| **Encryption** | `cryptography.fernet` |
| **Fuzzy Matching** | `thefuzz` + `rapidfuzz` |
| **HTML Parsing** | BeautifulSoup4 + lxml |
| **Logging** | structlog (JSON output) |
| **Rate Limiting** | slowapi |
| **Config** | pydantic-settings |
| **Testing** | pytest + pytest-cov + httpx |
| **Linting** | Ruff |
| **Containerization** | Docker (multi-stage) |
| **CI/CD** | GitHub Actions |

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Ensure all tests pass: `pytest tests/ -v`
- Run the linter: `ruff check .`
- Format code: `ruff format .`
- Add tests for any new functionality

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.

---

<div align="center">

**Made with ❤️ and 🤖 — keep your inbox clean without spending a penny.**

[Report Bug](https://github.com/yashshinde0080/Gmail-labeler-AI/issues) · [Request Feature](https://github.com/yashshinde0080/Gmail-labeler-AI/issues)

</div>
