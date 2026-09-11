# On Track

A single-user habit and spending tracker built with FastAPI, PostgreSQL, React,
and TypeScript. It combines manual daily check-ins with optional Gmail receipt,
Google Calendar, and SimpleFIN bank ingestion.

- Weekly targets for delivery orders, gym sessions, social events, alcohol days,
  and substances.
- Day navigation and backfilled check-ins.
- Ride and date tracking, spending breakdowns, bank flows, labels, and user overrides.
- Optional AI classifications and weekly reflections, with private metrics excluded
  from outbound scorecards and reflection prompts.

This application is designed for one owner. It is not a multi-tenant service.

## Setup

Read [MUST-DO-FIRST.md](MUST-DO-FIRST.md) before executing scripts.

Use Python 3.11 and Node 22.12 or newer within Node 22 (or Node 24).

```sh
python3.11 -m venv venv
venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
```

Set a strong, unique `APP_PASSWORD`. Set `ANTHROPIC_API_KEY` if using AI features.
Leave optional integration credentials unset until you intend to access those
services. An empty `DATABASE_URL` selects local SQLite; use a dedicated scratch
database for development. `.env.example` documents the complete configuration.

```sh
venv/bin/python -m uvicorn main:app --reload --port 8080
```

In a separate terminal, run `npm ci` and `npm run dev` from `frontend/`.
The dev server proxies `/api` to port 8080. Session cookies require HTTPS: use a
trusted local HTTPS proxy for authenticated browser work. Do not weaken the
production cookie settings to make plain HTTP login work.

## Tests and publication checks

Install Gitleaks 8.30.1. Review any existing Git hooks, preserve them, and enable
the checked-in hook with `git config core.hooksPath .githooks`.

```sh
venv/bin/python scripts/test_safe.py
venv/bin/python -m ruff check .
python3 scripts/check_publication.py
```

From `frontend/`, run `npm test`, `npm run build`, and `npm audit`.
The build includes TypeScript checking. GitHub Actions runs these checks as well.

`scripts/test_safe.py` disables dotenv loading before application imports, clears
integration credentials, and supplies a scratch SQLite target. It is the supported
test entry point. Passing SQLite tests does not verify PostgreSQL migrations or
concurrency; validate those separately when changing database behavior.

## Private data and fixtures

Account examples are synthetic. Keep real account hints, snapshots, credentials,
exports, and incident reports outside the repository. Optional backfill role seeds
live at `~/.on-track/role-seeds.json` or a path passed with `--role-seeds`:

```json
[
  ["example bank", "1001", "spending"],
  ["example bank", "1002", "bills"]
]
```

Each entry is `[institution substring, account substring, role]`. Roles are
`spending`, `bills`, `savings`, `credit_card`, or `investment`. Missing default
configuration means no automatic role seeding. Existing user-assigned roles are
never changed. Backfill writes data; check the target before running it.

The publication checker scans the exact index, including initial commits and
partial staging. It refuses to proceed without Gitleaks. Optional private matching
strings belong in `.git/publication-private-patterns.json` as a JSON string list.
They are local-only and are not shared with CI or new clones. Do not put private
values or their low-entropy hashes into a tracked blocklist. Neither hooks nor
secret scanning can recognize every form of personal information.

## Deployment

The existing Railway monorepo configuration builds the frontend and runs Uvicorn.
Attach PostgreSQL, set configuration on the web service, and confirm the exact
project/environment/service before deploying. In-process schedules and login locks
assume one web process/replica. Startup immediately runs configured ingestion jobs.
Never connect a development copy to real accounts merely to test startup.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for architecture and invariant guidance.
Licensed under [MIT](LICENSE).
