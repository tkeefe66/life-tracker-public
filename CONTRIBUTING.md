# Contributing

## Boundaries

- `config.py` reads application environment variables. One-off scripts are the
  documented exception. `usage.py` is a shared reporter; the application supplies
  its configuration explicitly through `ai_metrics.py`.
- `database.py` owns SQL. Call database functions from routes/jobs/services.
- `metrics.py` and `bank_flows.py` are pure computation.
- `ai_metrics.py` owns model calls. Review cost before changing its model.
- `app/scorecard.py` and `app/money.py` connect stored data to domain computations.
- All data routes belong to the authenticated router. Preserve random server-side
  sessions, revocation, absolute expiry, secure cookies, login locking, body limits,
  security headers, and API cache controls.
- The in-process scheduler and login lock assume one process/replica. Revisit both
  before scaling. Construct cron triggers through `main._cron()`.

## Domain rules

- Weeks run Monday–Sunday. Check-ins may be backdated; future dates are rejected.
- Delivery, gym, social, alcohol, and substances are the scored metrics. Rides,
  dates, and bank flows are unscored. Private metrics remain visible in the UI,
  but must not reach Telegram scorecards or reflection prompts.
- Rides and deliveries before 4 AM belong to the previous day. SQL resolves
  `COALESCE(user_date, effective_date)`, exposing `day` and `auto_day`. A manual
  date nudge is limited to automatic day plus/minus one; returning to automatic
  stores NULL. Ride timestamps are immutable after insertion.
- Gmail ingestion includes Trash. Follow-up/tip emails and duplicate ride
  receipts need deduplication; a subject alone is not a unique trip identifier.
- Scans own derived columns; user overrides have separate columns and are never
  overwritten by scans. Resolve values in SQL consistently across all callers.
- Calendar removal is distinct from classification. Only consistent overrides
  for recurring series may teach social classification; one-off removals do not.
- Date classification is a literal word-boundary title rule with a separate user
  override. Resolved dates are excluded from non-date social counts and spend.
- Bank sync reclassifies the entire table so changed account roles apply to
  history. A triaged row is excluded by `user_flow IS NOT NULL`, not `ambiguous`.
- User labels, notes, role/nickname choices, and no-label verdicts survive sync.
  Suggested flows are advisory; they never affect aggregates. Label suggestions
  recompute; durable no-label verdicts exclude rows from suggestions and bulk apply.
- Balances and holdings are not stored. The investments route computes on demand.
  Missing dollar amounts are unknown, not zero. Use null checks, not truthiness.

## Privacy and operations

- Ingestion failure statuses come from `services.safe_status`, never exception
  strings. Credential-bearing URLs must not reach responses, settings, or alerts.
  Keep `httpx` and `httpcore` loggers at WARNING. Handle logs as sensitive because
  server-side exceptions can contain integration details.
- Bank notes never reach AI. Only the explicitly selected fields for triage
  suggestions may reach the model. Bank records remain outside reflection and
  weekly scorecard content; do not serialize whole records into prompts.
- Telegram's weekly push is opt-in. Operational security/backup alerts use the
  configured bot independently of that toggle; do not send test notifications
  without authorization.
- Backups need independent key recovery and actual verification. Never force a
  backup, prune remote objects, or restore merely to exercise a test path.
- New columns need both PostgreSQL and SQLite migrations. Never rely on
  `CREATE TABLE IF NOT EXISTS` to add a column to an existing table.
- Pin Python dependencies exactly. Keep new configuration documented in
  `.env.example`. Validate required configuration before startup.

## Verification and design

Use `scripts/test_safe.py`, Ruff, frontend tests, and the TypeScript/Vite build.
Security guards must be tested against the failure they claim to prevent, using
random never-used secret fixtures where appropriate. Date tests should use fixed
dates or explicit week bounds rather than assuming yesterday is in this week.

The UI uses shared OKLCH tokens and hand-built responsive SVG charts. Preserve
both themes, accessible focus states, non-color indicators, and reduced motion.
Check chart-color contrast and separation before changing chart series colors.
See `DESIGN.md` and `PRODUCT.md` for visual/product conventions.

Enable `.githooks` after reviewing/preserving existing hooks. The checker reads
staged content rather than the working tree. CI repeats committed-content checks,
but local private matching patterns are intentionally not published or shared.
Review new documents, screenshots, and integrations for personal data manually.
