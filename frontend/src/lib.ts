const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parseDay(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function weekLabel(weekStartIso: string): string {
  const start = parseDay(weekStartIso);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return `${MONTHS[start.getMonth()]} ${start.getDate()} – ${MONTHS[end.getMonth()]} ${end.getDate()}`;
}

export function targetLabel(direction: string, target: number): string {
  return `${direction === "ceiling" ? "≤" : "≥"}${target}`;
}

/**
 * The y-axis top for a trend chart: the tallest bar and the target line both
 * need to sit comfortably inside the plot, never flush with the top edge (a
 * flush target line reads as "off the chart" rather than "the ceiling").
 */
export function niceMax(values: number[], target: number): number {
  const rawMax = Math.max(...values, target, 1);
  let max = Math.ceil(rawMax);
  if (max === target) max += 1;
  return max;
}

/**
 * A compact week-range label for chart axes: same-month weeks show the month
 * once ("Jul 13–19"), weeks crossing a month boundary repeat it ("Jun 29–Jul 5").
 */
export function weekRangeLabel(weekStartIso: string): string {
  const start = parseDay(weekStartIso);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const startMonth = MONTHS[start.getMonth()];
  const endMonth = MONTHS[end.getMonth()];
  return startMonth === endMonth
    ? `${startMonth} ${start.getDate()}–${end.getDate()}`
    : `${startMonth} ${start.getDate()}–${endMonth} ${end.getDate()}`;
}

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

export function dayLabel(iso: string): string {
  const d = parseDay(iso);
  return `${DAYS[d.getDay()]}, ${MONTHS_FULL[d.getMonth()]} ${d.getDate()}`;
}

export function addDays(iso: string, delta: number): string {
  const d = parseDay(iso);
  d.setDate(d.getDate() + delta);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** Valid day-nudge targets for a Day log row: the automatic day and its two
 * neighbors (the server's ±1 bound; landing on the automatic day clears the
 * override), minus the day the row currently sits on, minus the future.
 * ISO strings compare lexicographically, so <= is a real date compare. */
export function nudgeOptions(autoDay: string, viewedDay: string, todayIso: string): string[] {
  return [addDays(autoDay, -1), autoDay, addDays(autoDay, 1)]
    .filter((d) => d !== viewedDay && d <= todayIso);
}

/** Short label for a nudge button: "Jul 29". */
export function nudgeLabel(iso: string): string {
  const d = parseDay(iso);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

export function mondayOf(iso: string): string {
  const d = parseDay(iso);
  return addDays(iso, -((d.getDay() + 6) % 7));
}

export function relativeDayLabel(iso: string, todayIso: string): string {
  if (iso === todayIso) return "Today";
  if (iso === addDays(todayIso, -1)) return "Yesterday";
  const d = parseDay(iso);
  const base = `${DAYS[d.getDay()].slice(0, 3)}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return d.getFullYear() === parseDay(todayIso).getFullYear()
    ? base
    : `${base}, ${d.getFullYear()}`;
}

export interface SocialEditState {
  loadedTitle: string;
  loadedIsSocial: boolean;
  loadedAmount: number | null;
  loadedIsDate: boolean;
  loadedLocation: string | null;
  title: string;
  isSocial: boolean;
  amountText: string;
  isDate: boolean;
  locationText: string;
}

export interface SocialPatch {
  title?: string;
  is_social?: boolean;
  amount?: number | null;
  removed?: boolean;
  is_date?: boolean | null;
  location?: string | null;
}

/** Left-hand label for a spend-subtotal row: rides get a "rides" suffix,
 * delivery services print as-is, and any social row collapses to "Social". */
export function serviceLabel(kind: string, service: string): string {
  if (kind === "ride") return `${service} rides`;
  if (kind === "social") return "Social";
  return service;
}

/** `$16.31`, whole dollars trimmed (`$20`) — a real `$0` still shows, it is
 * never treated as "nothing to show" (that's the caller's job via null checks). */
export function money(amount: number): string {
  return `$${amount.toFixed(2).replace(/\.00$/, "")}`;
}

/** A negative bank net renders as `−$12.50` (U+2212 minus before the `$`),
 * never `money()`'s own `$-12.50` — same convention `weekCaption` uses for
 * negative weeks. */
export function signedMoney(amount: number): string {
  return amount < 0 ? `−${money(Math.abs(amount))}` : money(amount);
}

/** Signed percent for investment gains: `+3.6%`, `−5%` — U+2212 for losses
 * (matching signedMoney), one decimal with a trailing `.0` trimmed. Callers
 * null-check first: a missing cost basis shows nothing, never `+0%`. */
export function signedPct(pct: number): string {
  const s = Math.abs(pct).toFixed(1).replace(/\.0$/, "");
  return pct < 0 ? `−${s}%` : `+${s}%`;
}

/**
 * Button -> `user_flow` mapping for the triage worklist (spec §6.1). The user
 * never sees the word "flow" or any of its six values — these are the plain-
 * language labels the buttons show, and this table is the whole correctness
 * surface: get a row wrong and a correction writes the wrong flow silently.
 * Two queues, two answer sets — "Moved it" (ambiguous/outflow queue) and
 * "Moved from another account" (inflow_unknown queue) are different buttons
 * that both resolve to `transfer`.
 */
export interface TriageChoice { label: string; flow: string }
export const TRIAGE_CHOICES: { outflow: TriageChoice[]; inflow: TriageChoice[] } = {
  outflow: [
    { label: "Spent it", flow: "spending" },
    { label: "Moved it", flow: "transfer" },
    { label: "Paid a card", flow: "card_payment" },
    { label: "Saved / invested", flow: "investment" },
  ],
  inflow: [
    { label: "It's income", flow: "income" },
    { label: "Moved from another account", flow: "transfer" },
    { label: "Refunded", flow: "refund" },
  ],
};

const FLOW_LABELS: Record<string, string> = {
  spending: "Spent it",
  card_payment: "Paid off cards",
  transfer: "Moved between accounts",
  investment: "Into investments",
  income: "Money in",
  refund: "Refunded",
};

/** Display label for a resolved bank flow (§5.2 row headings). An unresolved
 * or unrecognized value is returned as-is rather than throwing — a display
 * helper should degrade, not crash the money screen. */
export function flowLabel(flow: string): string {
  return FLOW_LABELS[flow] ?? flow;
}

/** Triage-row suggestion hint (spec §5): `"looks like: {label}"` for a flow
 * the AI suggested, using `flowLabel`'s plain language verbatim rather than a
 * separate wording. Unlike `flowLabel`, an unrecognized flow — or no
 * suggestion at all (`null`/`undefined`) — degrades to `""` (no hint), never
 * a raw token. An AI suggestion of `spending` on an outflow row highlights
 * the "Spent it" chip: that's the outflow queue's most common true answer,
 * and making the suggestion visible there prevents wasting the inference. */
export function suggestionHint(flow: string | null | undefined): string {
  if (flow == null || !(flow in FLOW_LABELS)) return "";
  return `looks like: ${flowLabel(flow)}`;
}

/** The permanent coverage footnote (spec §5.6 / §4): explains why data
 * before `covered_from` doesn't exist, rather than looking like a gap. No
 * coverage yet (`null`, e.g. before the first sync) renders nothing. */
export function coverageNote(coveredFrom: string | null): string {
  if (coveredFrom == null) return "";
  const { monthDay } = dayRowDate(coveredFrom);
  return `Bank data starts ${monthDay}. SimpleFIN keeps 90 days, so nothing before that exists.`;
}

export interface WeekSpendPoint { week_start: string; spending: number; partial: boolean }

/** Tap caption for a bar in the weekly bank-spending chart (spec §5.1): the
 * week range, the total, and "partial week" only when the week is partial —
 * a zero-spend week still shows `$0`, never an empty gap. A week that nets
 * negative (refunds outweighing spending) shows the true net with a U+2212
 * minus before the `$`, rather than `money()`'s own `$-12.50`. */
export function weekCaption(week: WeekSpendPoint): string {
  const suffix = week.partial ? " · partial week" : "";
  return `${weekRangeLabel(week.week_start)} · ${signedMoney(week.spending)}${suffix}`;
}

/**
 * Inserts thousands separators on top of `money()`'s cents/whole-dollar
 * trimming. The shared `money()` used everywhere else in the app (delivery,
 * ride, and social amounts — all comfortably sub-$1,000) is left untouched;
 * only the whole-account bank total in `trackedShareSentence` runs large
 * enough to need grouping.
 */
function moneyGrouped(amount: number): string {
  const formatted = money(amount);
  const match = formatted.match(/^\$(\d+)(\.\d+)?$/);
  if (!match) return formatted;
  const [, wholePart, decimalPart = ""] = match;
  const grouped = wholePart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `$${grouped}${decimalPart}`;
}

/** "Of that, the things you're tracking" sentence (spec §5.4): the tracked
 * (delivery/rides/social/dates) share of the whole-account bank total, stated
 * as prose rather than a gauge. `spent === 0` means there is nothing to be a
 * share of, so the sentence is suppressed entirely rather than reading
 * "$0 of the $0 above." */
export function trackedShareSentence(tracked: number, spent: number): string {
  if (spent === 0) return "";
  return `Delivery, rides, social and dates are ${moneyGrouped(tracked)} of the ${moneyGrouped(spent)} above.`;
}

interface SubtotalDelivery { service: string; amount: number | null }
interface SubtotalRide { service: string; amount: number | null; user_is_work: boolean | null }
interface SubtotalSocialEvent { amount: number | null; end_at?: string; is_date?: boolean }
interface SubtotalDay {
  deliveries: SubtotalDelivery[];
  rides: SubtotalRide[];
  social_events: SubtotalSocialEvent[];
}
export interface SpendRow { kind: string; service: string; amount: number }

/**
 * Client-side mirror of the backend's spend_by_service shape, computed from a
 * single day's /today payload. Null amounts are skipped (nothing to sum), a
 * confirmed work ride (user_is_work === true) is excluded — matching the
 * backend's _personal_rides rule, an AI flag alone never excludes a ride —
 * and social events collapse into one "Social" row regardless of source. A
 * social event whose end_at hasn't happened yet is excluded from the total
 * (it still shows under "Noticed quietly") so "Spent today" agrees with
 * Week, which gates social spend on the event having occurred.
 */
export function subtotalsFromDay(day: SubtotalDay, now: number = Date.now()): SpendRow[] {
  const sums = new Map<string, SpendRow>();
  const add = (kind: string, service: string, amount: number | null) => {
    if (amount == null) return;
    const key = `${kind}:${service}`;
    const existing = sums.get(key);
    if (existing) existing.amount += amount;
    else sums.set(key, { kind, service, amount });
  };

  for (const d of day.deliveries) add("delivery", d.service, d.amount);
  for (const r of day.rides) {
    if (r.user_is_work) continue;
    add("ride", r.service, r.amount);
  }
  for (const e of day.social_events) {
    if (e.end_at !== undefined && new Date(e.end_at).getTime() > now) continue;
    // Dates ride in the same social_events payload but are their own spend
    // kind everywhere else (backend _spend_by_service) — "Spent today" must
    // agree with Week/Money, not lump them under Social.
    if (e.is_date) add("date", "Dates", e.amount);
    else add("social", "Social", e.amount);
  }

  return Array.from(sums.values())
    .map((r) => ({ ...r, amount: Math.round(r.amount * 100) / 100 }))
    .sort((a, b) => b.amount - a.amount);
}

export interface DayItem {
  kind: "delivery" | "ride" | "social" | "date";
  service: string;
  label: string;
  at: string;
  amount: number;
  is_work: boolean;
}

export interface Day {
  date: string;
  gym: boolean;
  alcohol_level: number | null;
  substances: boolean;
  total: number;
  items: DayItem[];
}

export interface Chip { label: string; tone: "accent" | "over" }

/** Splits a date into the two lines a week-day row's date button shows:
 * a 3-letter weekday ("Mon") over an abbreviated month + day ("Jul 20"). */
export function dayRowDate(iso: string): { weekday: string; monthDay: string } {
  const d = parseDay(iso);
  return { weekday: DAYS[d.getDay()].slice(0, 3), monthDay: `${MONTHS[d.getMonth()]} ${d.getDate()}` };
}

/**
 * Chips summarise a day, they do not enumerate it — one chip per category, not
 * one per item. Ceiling metrics (delivery, alcohol, substances) tint `over`;
 * floor metrics and rides tint `accent`. Work rides never produce a chip of
 * their own — they only surface in the expanded panel — so the ride count
 * here is personal rides only.
 */
export function dayChips(day: Day): Chip[] {
  const chips: Chip[] = [];
  if (day.gym) chips.push({ label: "Gym", tone: "accent" });
  if (day.items.some((i) => i.kind === "social")) chips.push({ label: "Social", tone: "accent" });
  // Without this, a date-only day has items but zero chips, which WeekDays
  // renders as "Work travel" — the only other items-but-no-chips case.
  const dateCount = day.items.filter((i) => i.kind === "date").length;
  if (dateCount > 0) chips.push({ label: dateCount === 1 ? "Date" : `${dateCount} dates`, tone: "accent" });
  if (day.alcohol_level != null) chips.push({ label: `Alcohol ${day.alcohol_level}`, tone: "over" });
  if (day.substances) chips.push({ label: "Substances", tone: "over" });

  const deliveryCount = day.items.filter((i) => i.kind === "delivery").length;
  if (deliveryCount > 0) chips.push({ label: `${deliveryCount} delivery`, tone: "over" });

  const rideCount = day.items.filter((i) => i.kind === "ride" && !i.is_work).length;
  if (rideCount > 0) chips.push({ label: `${rideCount} ride${rideCount === 1 ? "" : "s"}`, tone: "accent" });

  return chips;
}

/**
 * Diff the editor's current fields against what was loaded and return only the
 * fields the user actually changed — so PATCH never manufactures an override
 * (a pinned title, a bogus is_social flip) the user never made. An emptied cost
 * field becomes an explicit `null` (clears a stored amount) rather than being
 * omitted (which would leave a stale amount untouched).
 */
export function buildSocialPatch(state: SocialEditState): SocialPatch {
  const patch: SocialPatch = {};

  const trimmedTitle = state.title.trim();
  if (trimmedTitle !== state.loadedTitle) patch.title = trimmedTitle;

  if (state.isSocial !== state.loadedIsSocial) patch.is_social = state.isSocial;

  const amountText = state.amountText.trim();
  if (amountText === "") {
    if (state.loadedAmount !== null) patch.amount = null;
  } else {
    const amount = Number(amountText);
    if (amount !== state.loadedAmount) patch.amount = amount;
  }

  if (state.isDate !== state.loadedIsDate) patch.is_date = state.isDate;

  // Same emptied-field convention as amount: cleared text explicitly nulls
  // the stored override rather than silently leaving it in place.
  const locationText = state.locationText.trim();
  const loadedLocation = state.loadedLocation ?? "";
  if (locationText !== loadedLocation) {
    patch.location = locationText === "" ? null : locationText;
  }

  return patch;
}

/**
 * The patch sent when the user answers the ambiguity chip ("social? Yes /
 * No") on an `uncertain` event (spec: 2026-07-30-social-classification-
 * granularity-design). Always a full is_social answer — unlike
 * buildSocialPatch, which diffs against previously-loaded editor state,
 * tapping the chip IS the answer, not an edit of some other loaded value.
 */
export function buildUncertainResolvePatch(isSocial: boolean): SocialPatch {
  return { is_social: isSocial };
}

/**
 * Merges freshly-fetched social events with locally-tracked "Didn't happen"
 * removals (spec: 2026-07-29-social-event-didnt-happen-design) so a just-removed
 * row keeps rendering — with its own Undo action — even though the backend day
 * query (`get_events_for_day`) no longer returns an event once `is_social`
 * resolves false. Never relies on a refetch to restore the row; the caller
 * supplies the removed event's own last-known data. Re-sorted by start time so
 * a restored slot lands back where it was chronologically, rather than at
 * whatever position the removal/undo happened to leave it.
 */
export function mergeRemovedSocialEvents<T extends { gcal_event_id: string; start_at: string }>(
  fetched: T[],
  removed: Record<string, T>,
): T[] {
  const extra = Object.values(removed).filter(
    (e) => !fetched.some((f) => f.gcal_event_id === e.gcal_event_id),
  );
  return [...fetched, ...extra].sort(
    (a, b) => new Date(a.start_at).getTime() - new Date(b.start_at).getTime(),
  );
}

/**
 * Day log (spec: 2026-07-30-day-log-redesign-design) — the closed six-category
 * set (§3): food, transport, social, drink, fitness, money. Adding a seventh
 * is a deliberate design event, same weight as adding a chart color. Icons
 * are emoji glyphs only — no color fields (§4, mockup option B) — so no
 * per-category color tokens exist here on purpose.
 */
export type DayLogCategory = "food" | "transport" | "social" | "drink" | "fitness" | "money";

export const DAY_LOG_CATEGORY_ORDER: DayLogCategory[] = [
  "food", "transport", "social", "drink", "fitness", "money",
];

const DAY_LOG_GLYPHS: Record<DayLogCategory, string> = {
  food: "🍔", transport: "🚗", social: "🎉", drink: "🍺", fitness: "🏋", money: "💰",
};

/** The emoji glyph for a category — the only visual encoding a category ever
 * gets (§4). Shared by CategoryIcon (Day log rows, FilterStrip) and the Week
 * screen's SpendSubtotals rows (§8). */
export function categoryGlyph(category: DayLogCategory): string {
  return DAY_LOG_GLYPHS[category];
}

/**
 * Row-kind → category (§5: "icons encode source, never inference"). A
 * delivery-app order is `food` because it came from a delivery service, full
 * stop — never a guess about what was ordered. `kind` values match the
 * existing METRICS keys (`delivery`, `ride`, `social`, `alcohol`, `gym`) so
 * this doubles as the mapping for SpendSubtotals' `SpendRow.kind`. Any
 * unrecognized kind (future bank rows — see Future work) falls back to
 * `money` rather than throwing, since a display helper should degrade, not
 * crash the screen (same convention as `flowLabel`).
 */
export function categoryForKind(kind: string): DayLogCategory {
  switch (kind) {
    case "delivery": return "food";
    case "ride": return "transport";
    case "social": return "social";
    case "alcohol": return "drink";
    case "gym": return "fitness";
    default: return "money";
  }
}

/** Clock-time label for a Day log row (`7:37 PM`). An unparsable timestamp
 * degrades to "" rather than "Invalid Date" — a display helper should never
 * crash the screen over a bad string. */
export function dayLogTimeLabel(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/**
 * Row meta text (§6 row anatomy): `time · $amount`, time strictly to the
 * left of the amount — the bug this fixes is the old "Noticed quietly"
 * ordering (`$ · time`), which read backwards. Either half may be absent (a
 * check-in has no time; an amount-less social event has no cost) and the
 * join degrades gracefully rather than leaving a dangling " · ".
 */
export function dayLogRowMeta(timeIso: string | null, amount: number | null): string {
  const parts: string[] = [];
  if (timeIso) {
    const label = dayLogTimeLabel(timeIso);
    if (label) parts.push(label);
  }
  if (amount != null) parts.push(money(amount));
  return parts.join(" · ");
}

/**
 * Feed ordering (§1, §6): one chronological story. Check-ins carry no time
 * (`timeIso: null`) — they are placed after every timed row rather than
 * interleaved by insertion order, per the spec's explicit placement rule.
 * Both partitions are otherwise stable: equal-time items and the untimed
 * group keep their input order.
 */
export function orderDayLog<T extends { timeIso: string | null }>(items: T[]): T[] {
  const timed = items.filter((i) => i.timeIso !== null);
  const untimed = items.filter((i) => i.timeIso === null);
  timed.sort((a, b) => new Date(a.timeIso as string).getTime() - new Date(b.timeIso as string).getTime());
  return [...timed, ...untimed];
}

/** Categories actually present in a day's rows, in the canonical six-category
 * order — never the full six, and never in insertion order (FilterStrip's
 * icon order should stay stable regardless of which row happened to load
 * first). */
export function presentCategories<T extends { category: DayLogCategory }>(items: T[]): DayLogCategory[] {
  const present = new Set(items.map((i) => i.category));
  return DAY_LOG_CATEGORY_ORDER.filter((c) => present.has(c));
}

/** Filter predicate (§2): filtered-out rows dim, they never unmount — a day's
 * shape stays visible even while narrowed to one category. No active filter
 * (`null`) dims nothing. */
export function isDimmed(category: DayLogCategory, active: DayLogCategory | null): boolean {
  return active !== null && active !== category;
}

/**
 * Whether Google integration is broken badly enough to warn on EVERY screen,
 * not just Settings (CLAUDE.md: "Google auth expiry surfaces as a visible
 * banner in the app, never silent missing data"). Only `"error: auth"`
 * qualifies: it needs user action (rerun `scripts/calendar_auth.py`) and
 * never self-heals. Other error statuses (unreachable, rate limited, see
 * logs) are usually transient and stay Settings-only. `"error: Google not
 * configured"` also starts with `"error"` but is a deliberate no-op — Google
 * was never wired up, which isn't a *break* — so it must never trigger this
 * app-wide banner either.
 */
export function googleAuthBroken(
  gmailStatus: string | null,
  calendarStatus: string | null
): boolean {
  return gmailStatus === "error: auth" || calendarStatus === "error: auth";
}

export interface VendorLine { vendor: string; count: number; amount: number }
export interface VendorTail { vendors: number; count: number; amount: number }

// Top-N + "Everything else" split for the vendor breakdown. A tail of one
// vendor is promoted into `top` — "Everything else (1 vendor)" would be
// longer than the line it hides.
export function vendorSplit(lines: VendorLine[], topN: number = 15):
  { top: VendorLine[]; tail: VendorTail | null; rest: VendorLine[] } {
  if (lines.length <= topN + 1) return { top: lines, tail: null, rest: [] };
  const top = lines.slice(0, topN);
  const rest = lines.slice(topN);
  const tail = {
    vendors: rest.length,
    count: rest.reduce((s, l) => s + l.count, 0),
    amount: rest.reduce((s, l) => s + l.amount, 0),
  };
  return { top, tail, rest };
}
