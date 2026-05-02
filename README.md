# TikTok Factory — Automated Faceless Video Pipeline

An end-to-end pipeline that generates, assembles, and publishes faceless TikTok videos
across multiple content niches. Scripts are generated via the Claude API, voiceovers via
ElevenLabs TTS, visuals sourced from Pexels/Pixabay stock footage, and the final video
is published directly to TikTok.

---

## Architecture

```
script_generator  →  video_assembly  →  pipeline (queue + scheduler)  →  publisher
   (Claude API)       (FFmpeg/TTS)        (SQLite + APScheduler)         (TikTok API)
```

| Module | Purpose |
|---|---|
| `script_generator` | Claude-powered script generation with niche-aware prompts |
| `video_assembly` | FFmpeg video assembly in three formats (A/B/C), captions, TTS |
| `pipeline` | Job queue, batch scheduler, and full orchestration CLI |
| `publisher` | TikTok upload client and niche post scheduler |
| `configs/` | Niche YAML configuration (hashtags, cron schedules, privacy settings) |
| `tools/` | Standalone utilities (trending audio CLI) |

---

## Requirements

- Python 3.9+
- FFmpeg installed and on `PATH`

---

## Setup

### 1. Clone the repository

```bash
git clone git@github.com:WalidZein/paperclip-test.git
cd paperclip-test
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file (or export vars directly):

```bash
# Required — AI & media providers
ANTHROPIC_API_KEY=sk-ant-...          # Claude API for script generation
ELEVENLABS_API_KEY=...                # ElevenLabs TTS
PEXELS_API_KEY=...                    # Pexels stock footage

# Optional
PIXABAY_API_KEY=...                   # Pixabay stock footage (fallback)
TIKTOK_ACCESS_TOKEN=...              # TikTok OAuth access token (required for publishing)
SLACK_WEBHOOK_URL=https://hooks.slack.com/...  # Slack alerts (optional)
```

Load the file before running:

```bash
export $(grep -v '^#' .env | xargs)
```

---

## Running the Pipeline

### Prepare a content brief

Create a JSON file describing the video to produce:

```json
{
  "niche": "finance",
  "topic": "save $1000 per month on a $50k salary",
  "format": "format_a",
  "duration_seconds": 45
}
```

Supported niches: `cooking`, `fitness`, `finance`, `ai_tech`, `motivation`,
`dark_psychology`, `personal_finance`.

Supported formats: `format_a` (text overlay + stock footage), `format_b` (card layout),
`format_c` (caption-driven).

### Submit jobs to the queue

```bash
python -m pipeline submit brief.json
# Submit multiple at once
python -m pipeline submit briefs/*.json
```

### Run the queue immediately (one-shot)

```bash
python -m pipeline run-now
```

Videos are written to `output/<date>/<niche>/<slug>/`.

### Start the persistent scheduler

The scheduler runs a drain cycle every 30 minutes and sends daily Slack reports:

```bash
python -m pipeline run
```

### Watch a directory for new briefs

Drop JSON files into a folder and have them auto-enqueued:

```bash
python -m pipeline watch ./inbox --interval 10
```

### Check queue status

```bash
python -m pipeline status
```

### Print today's run report

```bash
python -m pipeline report
```

### Advanced options

```
python -m pipeline <command> \
  --db pipeline_jobs.db      # SQLite path (default: pipeline_jobs.db)
  --output output            # Output directory
  --failed failed            # Failed-job directory
  --report daily_report.txt  # Report file path
  --cache .stock_cache       # Stock footage cache directory
```

---

## Getting a TikTok Access Token

Publishing requires a TikTok Developer App and a user-authorized OAuth2 token.

### 1. Create a TikTok Developer App

1. Go to [developers.tiktok.com](https://developers.tiktok.com) and log in.
2. Create a new app — set the category to **Content Posting**.
3. Under **Products**, enable **Content Posting API**.
4. Add your redirect URI (can be `http://localhost:8000/callback` for local use).
5. Copy your **Client Key** and **Client Secret** from the app dashboard.

### 2. Run the auth helper

```bash
python tools/tiktok_auth.py \
  --client-key YOUR_CLIENT_KEY \
  --client-secret YOUR_CLIENT_SECRET \
  --redirect-uri http://localhost:8000/callback
```

The script will:
1. Print an authorization URL — open it in your browser and approve the app.
2. TikTok redirects to your `redirect_uri` with a `?code=` query parameter.
3. Paste that `code` back into the terminal.
4. The script exchanges it for tokens and prints the values to add to your `.env`.

```
TIKTOK_ACCESS_TOKEN=act.xxxxxxxx...
TIKTOK_REFRESH_TOKEN=rft.xxxxxxxx...
TIKTOK_OPEN_ID=xxxxxxxx...
```

### 3. Refresh an expired token

Access tokens expire (typically 24 hours). Use the OAuth2 client directly to refresh:

```python
from publisher.tiktok_client import TikTokOAuth2Client

client = TikTokOAuth2Client(
    client_key="YOUR_CLIENT_KEY",
    client_secret="YOUR_CLIENT_SECRET",
    redirect_uri="http://localhost:8000/callback",
)
token = client.refresh_access_token("YOUR_REFRESH_TOKEN")
print(token.access_token)
```

---

## Publishing to TikTok

### One-shot publish a video

```bash
python -m publisher \
  --config configs/niches.yaml \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  publish \
  --video output/2026-05-02/finance/save-1000/final.mp4 \
  --niche finance \
  --topic "save 1000 a month"
```

### Start the automated post scheduler

Runs cron-based scheduling for all niches defined in `configs/niches.yaml`:

```bash
python -m publisher \
  --config configs/niches.yaml \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  schedule \
  --video-dir output/
```

---

## Niche Configuration

Edit `configs/niches.yaml` to customize hashtags, post schedules, and privacy per niche:

```yaml
niches:
  finance:
    description_template: "How to {topic} and grow your wealth 💰"
    hashtags:
      - "#personalfinance"
      - "#investing"
    schedule:
      cron: "0 12 * * 1-5"   # noon on weekdays
      timezone: "America/New_York"
```

---

## Running Tests

```bash
pytest
```

Run a specific test module:

```bash
pytest tests/test_pipeline.py -v
```

---

## Project Structure

```
.
├── configs/
│   └── niches.yaml             # Niche hashtags, schedules, privacy settings
├── pipeline/
│   ├── __main__.py             # Entry point: python -m pipeline
│   ├── cli.py                  # CLI subcommands (submit/run/status/report/watch)
│   ├── queue.py                # SQLite job queue
│   ├── runner.py               # Full pipeline execution per job
│   ├── scheduler.py            # APScheduler-based batch scheduler
│   └── alerts.py               # Slack webhook alerter
├── publisher/
│   ├── cli.py                  # Entry point: python -m publisher
│   ├── uploader.py             # TikTok upload client
│   ├── scheduler.py            # Cron-based post scheduler
│   └── tiktok_client.py        # TikTok API wrapper
├── script_generator/
│   └── generator.py            # Claude API script generation
├── video_assembly/
│   ├── assembler.py            # Top-level video assembly coordinator
│   ├── pipeline_format_a.py    # Format A: text overlay + stock footage
│   ├── format_b.py             # Format B: card layout renderer
│   ├── format_c.py             # Format C: caption-driven renderer
│   ├── tts.py                  # ElevenLabs TTS with word timestamps
│   ├── captions.py             # Caption overlay generation
│   ├── audio_mixer.py          # Background audio mixing
│   ├── stock_footage.py        # Pexels/Pixabay footage fetcher + cache
│   └── profiles/               # Per-niche visual profiles (YAML)
├── tools/
│   └── trending_audio_cli.py   # Trending audio discovery utility
├── tests/                      # pytest test suite
├── requirements.txt
└── .gitignore
```
