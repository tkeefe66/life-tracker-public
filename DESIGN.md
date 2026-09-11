# On Track — Design System

Register: product. Personality: **calm ledger** (see PRODUCT.md). Channels Linear /
Things: quiet precision, hairlines, soft depth, restraint. All tokens live in
`frontend/src/styles.css`; components style through tokens only.

## Color

OKLCH throughout; all neutrals tinted 0.003–0.018 chroma toward the accent hue (277).
Both themes are first-class; dark is not an inversion but its own tuning.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `oklch(97.3% 0.004 277)` | `oklch(19% 0.01 277)` | Page ground |
| `--surface` | white | `oklch(23.5% 0.012 277)` | Rows, groups |
| `--surface-2` | `oklch(98.6% 0.003 277)` | `oklch(21.5% 0.011 277)` | Tab bar, inputs, chips |
| `--ink` / `--ink-2` / `--muted` | 22% / 42% / 50% L | 93% / 76% / 66% L | Text hierarchy |
| `--line` | `oklch(90.5% 0.006 277)` | `oklch(30.5% 0.014 277)` | Hairlines |
| `--accent` | `oklch(50% 0.185 277)` indigo | `oklch(67% 0.155 277)` | Action + completion, ONLY |
| `--over` | `oklch(56% 0.135 60)` amber | `oklch(73% 0.125 65)` | Over-target, ONLY |
| `--danger` | `oklch(52% 0.19 27)` | `oklch(68% 0.17 25)` | Errors only |

Rules: indigo = action/completion; amber = over-target; that's the entire emotional
range. No greens, no reds for scoring. Hit/miss always carries a shape cue (filled dot
vs. outlined dot, filled bar vs. outlined bar) — never color alone.

## Typography

One family: the system stack (`-apple-system, system-ui, "Segoe UI", Roboto`).
Fixed rem scale: screen title 1.35rem/600/-0.02em · row title 0.95rem/500 ·
secondary 0.8rem · footnotes 0.78rem. All counts use `font-variant-numeric:
tabular-nums` (`.num`). Section labels are small sentence-case muted text
("Noticed quietly", "This week") — never uppercase-tracked eyebrows.

## Components

- **`.item`** — check-in row: surface, hairline border, 12px radius, soft shadow,
  26px status dot. Done state fills the dot indigo with a 200ms "settle" pop.
- **`.chips`** — alcohol levels 1–3, square tap targets, one-tap logging.
- **`.quiet`** — unboxed detection rows, hairline-separated, timestamp right-aligned.
- **`.meter`** — 4px progress bar; `.over` variant switches fill to amber.
- **`.ledger` / `.metric`** — scorecard: one surface, hairline-divided metric rows,
  each with mark dot + count, meter, sub-line, 8-week `.hist` mini-bars.
- **`.group` / `.row`** — settings: iOS-style grouped lists, `.switch` toggle,
  `.field-num` numeric input.
- **Tab bar** — fixed bottom, `surface-2`, 20px line icons + labels
  (Today / Week / Settings), active = accent.

## Motion

160ms `cubic-bezier(.22,.61,.36,1)` on state changes (color, border, transform);
meters animate width at 300ms; check-in dot pops once on completion. Press feedback:
scale 0.985 on rows, 0.92 on chips. No page-load choreography. Full
`prefers-reduced-motion` kill switch.

## Voice

Plain, kind, judgment-free: "Tap when you've been", "Noticed quietly",
"Nothing today.", "Within target". Never scolding, never gamified.
