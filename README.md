# Gmail AI Auto Labeler

An intelligent Gmail assistant that monitors your inbox, classifies
every email with the **Groq AI API** (free tier), creates Gmail labels
automatically, stars important messages, and archives clutter — all
running for **0 INR / 0 USD** in production.

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/your-username/gmail-ai-labeler.git
cd gmail-ai-labeler
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Fill in your credentials — see the "Credentials Setup" section below
```

### 4. Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/docs` for the interactive API docs.

---

## Credentials Setup

### Groq API Key

1. Create an account at <https://console.groq.com>
2. Go to **API Keys** → **Create API Key**
3. Copy the key into `GROQ_API_KEY` in your `.env`

### Gmail OAuth2

#### Step 1 — Google Cloud Project

1. Go to <https://console.cloud.google.com>
2. Create a new project (e.g. `gmail-ai-labeler`)
3. **APIs & Services** → **Enable APIs** → search for **Gmail API** → Enable

#### Step 2 — OAuth Consent Screen

1. **APIs & Services** → **OAuth consent screen**
2. User type: **External** (or Internal if using Google Workspace)
3. Fill in app name, support email, developer email
4. Add scope: `https://www.googleapis.com/auth/gmail.modify`
5. Add your Gmail address as a **Test user**

#### Step 3 — OAuth Credentials & Refresh Token

1. **APIs & Services** → **Credentials** → **Create Credentials**
   → **OAuth 2.0 Client ID**
2. Application type: **Desktop app**
3. ### Production Deployment

#### Standalone (Railway / Render / Docker)
The application includes a `Dockerfile` for easy deployment.
1. Deploy the Docker image to your provider.
2. Set the following environment variables:
   - `DATABASE_URL` (Postgres)
   - `FERNET_KEY` (32-byte base64 string)
   - `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_PROJECT_ID`
3. The background scheduler will run automatically within the container.

#### Serverless (Vercel)
Vercel does not support background threads.
1. Set up a Postgres database (e.g. Neon, Supabase) and add the URL to `DATABASE_URL`.
2. Disable the scheduler: `SCHEDULER_ENABLED=false`
3. Set up Vercel Cron to hit the `/api/cron/process` endpoint every minute.

Or with Docker Compose:

```bash
docker compose up --build -d
docker compose logs -f
```

---

## Deploy

### Vercel

```bash
npx vercel --prod
# Set env vars in Vercel dashboard:
#   GROQ_API_KEY, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET
# Note: scheduler is disabled on Vercel (no background process).
# Use POST /sync endpoint via cron job instead.
```

### Render

1. Push your code to GitHub
2. Go to <https://render.com> → **New** → **Blueprint**
3. Connect your GitHub repository
4. Set env vars in Render dashboard:
   - `GROQ_API_KEY`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`
5. Click **Deploy**
6. Verify: `https://your-service.onrender.com/health`

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Welcome message |
| `GET` | `/health` | Liveness probe |
| `GET` | `/stats` | Email processing statistics |
| `GET` | `/labels` | All known Gmail labels |
| `GET` | `/logs` | Recent processed emails (paginated) |
| `POST` | `/sync` | Manually trigger a processing cycle |
| `POST` | `/reprocess` | Re-classify a specific message |
| `GET` | `/metrics` | AI token usage and latency |

Full interactive docs: `/docs`

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | **Required** |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `GROQ_TIMEOUT` | `30` | API timeout in seconds |
| `GMAIL_CLIENT_ID` | — | **Required** |
| `GMAIL_CLIENT_SECRET` | — | **Required** |
| `POLL_INTERVAL_SECONDS` | `10` | How often to check inbox (min 10) |
| `CONFIDENCE_THRESHOLD` | `70` | Minimum AI confidence to act |
| `ARCHIVE_LOW_IMPORTANCE` | `true` | Archive newsletters/promotions |
| `STAR_HIGH_IMPORTANCE` | `true` | Star urgent emails |
| `ENABLE_NESTED_LABELS` | `false` | Allow `Work/AI` style labels |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `SCHEDULER_ENABLED` | `true` | Set false to disable auto-polling |

---

## Running Tests

```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## Project Structure

```
gmail-ai-labeler/
├── app/
│   ├── ai/
│   │   ├── classifier.py     # Classification orchestrator
│   │   ├── groq_client.py    # Groq SDK wrapper with retry
│   │   └── prompt.py         # Prompt templates and category list
│   ├── gmail/
│   │   ├── auth.py           # OAuth2 credentials management
│   │   ├── labels.py         # Label CRUD + fuzzy matching
│   │   ├── messages.py       # Message fetch + parse + clean
│   │   └── service.py        # Gmail API service builder
│   ├── database/
│   │   ├── db.py             # Engine, session factory, init_db
│   │   ├── models.py         # SQLAlchemy ORM models
│   │   └── crud.py           # Database helper functions
│   ├── api.py                # FastAPI routes
│   ├── config.py             # Settings (pydantic-settings)
│   ├── logger.py             # Structured logging (structlog)
│   ├── main.py               # Application entry point
│   └── scheduler.py          # APScheduler jobs
├── tests/
│   ├── test_classifier.py
│   ├── test_labels.py
│   └── test_api.py
├── data/                     # Created at runtime (gitignored)
│   ├── gmail.db
│   └── logs/
├── Dockerfile
├── docker-compose.yml
├── render.yaml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Cost

| Component | Cost |
|-----------|------|
| Groq API | Free (rate-limited) |
| Gmail API | Free |
| SQLite | Free |
| Render (free plan) | Free |
| GitHub Actions | Free |
| **Total** | **0 INR / 0 USD** |

---

## Licence

MIT
