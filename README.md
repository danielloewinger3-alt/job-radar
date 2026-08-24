# Job Radar

A local web app that polls job boards for entry-level/early-career tech roles in your target
cities (London, Tel Aviv, Miami, New York, Silicon Valley, Boston, Chicago, Austin, Dallas, plus
an EU net reachable via a Hungarian passport) and shows them as pings on a map.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional: add Adzuna/Reed/USAJobs API keys
```

## Run

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

Then open http://localhost:8000

On startup it fetches immediately, then re-polls every `POLL_INTERVAL_MINUTES` (default 60,
set in `.env`). Click "Refresh now" in the UI to poll on demand.

## What's wired up

- **Greenhouse, Lever, RemoteOK** — public APIs, no key needed, work immediately. Company lists
  live in `backend/config.py` (`GREENHOUSE_COMPANIES`, `LEVER_COMPANIES`) — add more as you find
  companies that hire grads and use those ATSs.
- **Adzuna, Reed, USAJobs** — free but need an API key (sign-up links in `.env.example`). Each
  source silently no-ops if its key isn't set.

## How filtering works

`backend/config.py` holds the target cities (with name aliases used for text matching) and the
role-keyword/seniority-exclude lists. `backend/matching.py` applies them: a job is kept if its
title matches a tech-role keyword, isn't senior/staff/lead, and its location matches a target
city or is remote. Adjust the lists there to widen or narrow what shows up.

## Data

Jobs are stored in `jobs.db` (SQLite, gitignored). A job already seen before is never
re-flagged as new — that's what drives the "ping" (pulsing red ring) on the map and the red
left-border on job cards in the sidebar.
