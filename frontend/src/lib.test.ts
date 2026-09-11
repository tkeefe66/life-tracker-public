import { describe, expect, it } from "vitest";
import {
  dayChips, dayLabel, dayRowDate, money, serviceLabel, subtotalsFromDay, targetLabel, weekLabel, type Day,
  TRIAGE_CHOICES, flowLabel, suggestionHint, coverageNote, weekCaption, trackedShareSentence,
  signedMoney, signedPct, vendorSplit, type VendorLine, mergeRemovedSocialEvents, googleAuthBroken,
} from "./lib";

describe("weekLabel", () => {
  it("formats a Monday week start as a Mon–Sun range", () => {
    expect(weekLabel("2026-07-13")).toBe("Jul 13 – Jul 19");
  });
  it("crosses month boundaries", () => {
    expect(weekLabel("2026-07-27")).toBe("Jul 27 – Aug 2");
  });
});

describe("targetLabel", () => {
  it("renders ceiling and floor", () => {
    expect(targetLabel("ceiling", 1)).toBe("≤1");
    expect(targetLabel("floor", 3)).toBe("≥3");
  });
});

describe("dayLabel", () => {
  it("formats a date with weekday and full month", () => {
    expect(dayLabel("2026-07-20")).toBe("Monday, July 20");
  });
});

describe("dayRowDate", () => {
  it("splits a date into a 3-letter weekday and an abbreviated month + day", () => {
    expect(dayRowDate("2026-07-20")).toEqual({ weekday: "Mon", monthDay: "Jul 20" });
    expect(dayRowDate("2026-07-19")).toEqual({ weekday: "Sun", monthDay: "Jul 19" });
  });
});

import { addDays, relativeDayLabel } from "./lib";

describe("addDays", () => {
  it("steps forward and back across month boundaries", () => {
    expect(addDays("2026-07-01", -1)).toBe("2026-06-30");
    expect(addDays("2026-06-30", 1)).toBe("2026-07-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });
});

describe("relativeDayLabel", () => {
  it("labels today and yesterday", () => {
    expect(relativeDayLabel("2026-07-22", "2026-07-22")).toBe("Today");
    expect(relativeDayLabel("2026-07-21", "2026-07-22")).toBe("Yesterday");
  });
  it("labels older days with weekday and month", () => {
    expect(relativeDayLabel("2026-07-14", "2026-07-22")).toBe("Tue, Jul 14");
  });
  it("appends the year when it differs", () => {
    expect(relativeDayLabel("2025-12-31", "2026-07-22")).toBe("Wed, Dec 31, 2025");
  });
});

import { mondayOf } from "./lib";

describe("mondayOf", () => {
  it("maps a mid-week date to its containing Monday", () => {
    expect(mondayOf("2026-07-22")).toBe("2026-07-20"); // Wednesday
  });
  it("is identity on a Monday", () => {
    expect(mondayOf("2026-07-20")).toBe("2026-07-20");
  });
  it("maps Sunday back to the previous Monday", () => {
    expect(mondayOf("2026-07-26")).toBe("2026-07-20");
  });
  it("crosses year boundaries", () => {
    expect(mondayOf("2026-01-01")).toBe("2025-12-29"); // Thursday
  });
});

import { niceMax, weekRangeLabel } from "./lib";

describe("niceMax", () => {
  it("gives an all-zero series with a target of 3 a top of at least 3", () => {
    expect(niceMax([0, 0, 0], 3)).toBeGreaterThanOrEqual(3);
  });
  it("gives a max of 7 against a target of 1 a top of at least 7", () => {
    expect(niceMax([1, 7, 0], 1)).toBeGreaterThanOrEqual(7);
  });
  it("adds headroom above the target when the target is the series max, so the line isn't flush with the top", () => {
    const top = niceMax([0, 2, 3], 3);
    expect(top).toBeGreaterThan(3);
  });
  it("never returns less than 1", () => {
    expect(niceMax([], 0)).toBeGreaterThanOrEqual(1);
  });
});

describe("weekRangeLabel", () => {
  it("formats a week within a single month", () => {
    expect(weekRangeLabel("2026-07-13")).toBe("Jul 13–19");
  });
  it("formats a week crossing months", () => {
    expect(weekRangeLabel("2026-06-29")).toBe("Jun 29–Jul 5");
  });
});

import { buildSocialPatch, buildUncertainResolvePatch } from "./lib";

describe("buildSocialPatch", () => {
  const loaded = {
    loadedTitle: "Dinner", loadedIsSocial: true, loadedAmount: null,
    loadedIsDate: false, loadedLocation: null, isDate: false, locationText: "",
  };

  it("sends nothing when the editor is saved without any change", () => {
    const patch = buildSocialPatch({ ...loaded, title: "Dinner", isSocial: true, amountText: "" });
    expect(patch).toEqual({});
  });

  it("omits title and is_social when only the cost was added — no manufactured override", () => {
    const patch = buildSocialPatch({ ...loaded, title: "Dinner", isSocial: true, amountText: "25" });
    expect(patch).toEqual({ amount: 25 });
  });

  it("includes title only when the text actually differs from what was loaded", () => {
    const patch = buildSocialPatch({ ...loaded, title: "Dinner out", isSocial: true, amountText: "" });
    expect(patch).toEqual({ title: "Dinner out" });
  });

  it("includes is_social only when the checkbox was actually toggled", () => {
    const patch = buildSocialPatch({ ...loaded, title: "Dinner", isSocial: false, amountText: "" });
    expect(patch).toEqual({ is_social: false });
  });

  it("sends amount: null (not omitted) when a stored cost is cleared", () => {
    const withAmount = { ...loaded, loadedAmount: 300 };
    const patch = buildSocialPatch({ ...withAmount, title: "Dinner", isSocial: true, amountText: "" });
    expect(patch).toEqual({ amount: null });
  });

  it("omits amount when the field was empty and stays empty", () => {
    const patch = buildSocialPatch({ ...loaded, title: "Dinner", isSocial: true, amountText: "" });
    expect(patch.amount).toBeUndefined();
  });

  it("omits amount when the typed value matches the loaded amount", () => {
    const withAmount = { ...loaded, loadedAmount: 25 };
    const patch = buildSocialPatch({ ...withAmount, title: "Dinner", isSocial: true, amountText: "25" });
    expect(patch.amount).toBeUndefined();
  });

  it("combines multiple real changes", () => {
    const patch = buildSocialPatch({ ...loaded, title: "Dinner out", isSocial: false, amountText: "40" });
    expect(patch).toEqual({ title: "Dinner out", is_social: false, amount: 40 });
  });
});

describe("buildUncertainResolvePatch", () => {
  it("answers Yes with a plain is_social: true patch", () => {
    expect(buildUncertainResolvePatch(true)).toEqual({ is_social: true });
  });

  it("answers No with a plain is_social: false patch", () => {
    expect(buildUncertainResolvePatch(false)).toEqual({ is_social: false });
  });
});

describe("serviceLabel", () => {
  it("appends 'rides' to a ride service", () => {
    expect(serviceLabel("ride", "Uber")).toBe("Uber rides");
    expect(serviceLabel("ride", "Lyft")).toBe("Lyft rides");
  });

  it("leaves a delivery service label as-is", () => {
    expect(serviceLabel("delivery", "Uber Eats")).toBe("Uber Eats");
    expect(serviceLabel("delivery", "DoorDash")).toBe("DoorDash");
  });

  it("collapses any social service to 'Social'", () => {
    expect(serviceLabel("social", "Social")).toBe("Social");
    expect(serviceLabel("social", "anything")).toBe("Social");
  });
});

describe("money", () => {
  it("formats cents", () => {
    expect(money(16.31)).toBe("$16.31");
  });

  it("trims a whole-dollar amount", () => {
    expect(money(20)).toBe("$20");
  });

  it("shows a real zero rather than hiding it", () => {
    expect(money(0)).toBe("$0");
  });
});

describe("subtotalsFromDay", () => {
  it("sums a mixed day of deliveries, personal rides, and social spend", () => {
    const rows = subtotalsFromDay({
      deliveries: [
        { service: "Uber Eats", amount: 16.31 },
        { service: "Uber Eats", amount: 10 },
        { service: "DoorDash", amount: 8 },
      ],
      rides: [
        { service: "Uber", amount: 12, user_is_work: false },
      ],
      social_events: [{ amount: 25 }],
    });
    expect(rows).toEqual([
      { kind: "delivery", service: "Uber Eats", amount: 26.31 },
      { kind: "social", service: "Social", amount: 25 },
      { kind: "ride", service: "Uber", amount: 12 },
      { kind: "delivery", service: "DoorDash", amount: 8 },
    ]);
  });

  it("excludes a ride whose user_is_work is true", () => {
    const rows = subtotalsFromDay({
      deliveries: [],
      rides: [
        { service: "Uber", amount: 12, user_is_work: false },
        { service: "Lyft", amount: 50, user_is_work: true },
      ],
      social_events: [],
    });
    expect(rows).toEqual([{ kind: "ride", service: "Uber", amount: 12 }]);
  });

  it("returns an empty array when nothing has an amount", () => {
    const rows = subtotalsFromDay({
      deliveries: [{ service: "Uber Eats", amount: null }],
      rides: [{ service: "Uber", amount: null, user_is_work: false }],
      social_events: [{ amount: null }],
    });
    expect(rows).toEqual([]);
  });

  it("excludes a social event whose end_at hasn't happened yet", () => {
    const now = new Date("2026-07-22T12:00:00").getTime();
    const rows = subtotalsFromDay({
      deliveries: [],
      rides: [],
      social_events: [
        { amount: 25, end_at: "2026-07-22T08:00:00" }, // already ended — counted
        { amount: 80, end_at: "2026-07-22T21:00:00" }, // still to come — excluded
      ],
    }, now);
    expect(rows).toEqual([{ kind: "social", service: "Social", amount: 25 }]);
  });

  it("still counts a social event with no end_at (backward-compatible default)", () => {
    const rows = subtotalsFromDay({
      deliveries: [],
      rides: [],
      social_events: [{ amount: 25 }],
    });
    expect(rows).toEqual([{ kind: "social", service: "Social", amount: 25 }]);
  });
});

function emptyDay(overrides: Partial<Day> = {}): Day {
  return {
    date: "2026-07-20",
    gym: false,
    alcohol_level: null,
    substances: false,
    total: 0,
    items: [],
    ...overrides,
  };
}

describe("dayChips", () => {
  it("returns nothing for a day with nothing logged", () => {
    expect(dayChips(emptyDay())).toEqual([]);
  });

  it("chips gym, social, alcohol level, and substances", () => {
    const day = emptyDay({
      gym: true,
      alcohol_level: 2,
      substances: true,
      items: [
        { kind: "social", service: "Social", label: "Dinner", at: "2026-07-20T19:00:00", amount: 25, is_work: false },
      ],
    });
    expect(dayChips(day)).toEqual([
      { label: "Gym", tone: "accent" },
      { label: "Social", tone: "accent" },
      { label: "Alcohol 2", tone: "over" },
      { label: "Substances", tone: "over" },
    ]);
  });

  it("counts delivery items into an over-tinted N delivery chip", () => {
    const day = emptyDay({
      items: [
        { kind: "delivery", service: "Uber Eats", label: "Pizza", at: "2026-07-20T18:00:00", amount: 16, is_work: false },
        { kind: "delivery", service: "DoorDash", label: "Tacos", at: "2026-07-20T19:00:00", amount: 12, is_work: false },
      ],
    });
    expect(dayChips(day)).toEqual([{ label: "2 delivery", tone: "over" }]);
  });

  it("counts personal ride items into an accent-tinted N ride/N rides chip", () => {
    const oneRide = emptyDay({
      items: [{ kind: "ride", service: "Uber", label: "Trip", at: "2026-07-20T08:00:00", amount: 10, is_work: false }],
    });
    expect(dayChips(oneRide)).toEqual([{ label: "1 ride", tone: "accent" }]);

    const twoRides = emptyDay({
      items: [
        { kind: "ride", service: "Uber", label: "Trip 1", at: "2026-07-20T08:00:00", amount: 10, is_work: false },
        { kind: "ride", service: "Lyft", label: "Trip 2", at: "2026-07-20T18:00:00", amount: 14, is_work: false },
      ],
    });
    expect(dayChips(twoRides)).toEqual([{ label: "2 rides", tone: "accent" }]);
  });

  it("work rides do not produce a chip", () => {
    const day = emptyDay({
      items: [{ kind: "ride", service: "Uber", label: "Commute", at: "2026-07-20T08:00:00", amount: 10, is_work: true }],
    });
    expect(dayChips(day)).toEqual([]);
  });

  it("a day with one personal and one work ride chips only the personal one", () => {
    const day = emptyDay({
      items: [
        { kind: "ride", service: "Uber", label: "Personal", at: "2026-07-20T08:00:00", amount: 10, is_work: false },
        { kind: "ride", service: "Uber", label: "Work", at: "2026-07-20T09:00:00", amount: 20, is_work: true },
      ],
    });
    expect(dayChips(day)).toEqual([{ label: "1 ride", tone: "accent" }]);
  });
});

describe("TRIAGE_CHOICES", () => {
  it("maps the ambiguous-queue buttons to their exact user_flow, in spec order", () => {
    expect(TRIAGE_CHOICES.outflow).toEqual([
      { label: "Spent it", flow: "spending" },
      { label: "Moved it", flow: "transfer" },
      { label: "Paid a card", flow: "card_payment" },
      { label: "Saved / invested", flow: "investment" },
    ]);
  });

  it("maps the inflow-unknown-queue buttons to their exact user_flow, in spec order", () => {
    expect(TRIAGE_CHOICES.inflow).toEqual([
      { label: "It's income", flow: "income" },
      { label: "Moved from another account", flow: "transfer" },
      { label: "Refunded", flow: "refund" },
    ]);
  });
});

describe("flowLabel", () => {
  it("labels the known flow values", () => {
    expect(flowLabel("spending")).toBe("Spent it");
    expect(flowLabel("card_payment")).toBe("Paid off cards");
    expect(flowLabel("transfer")).toBe("Moved between accounts");
    expect(flowLabel("investment")).toBe("Into investments");
    expect(flowLabel("income")).toBe("Money in");
    expect(flowLabel("refund")).toBe("Refunded");
  });

  it("returns an unknown value as-is rather than throwing", () => {
    expect(flowLabel("something_new")).toBe("something_new");
  });
});

describe("suggestionHint", () => {
  it("prefixes the known flow's label", () => {
    expect(suggestionHint("refund")).toBe("looks like: Refunded");
    expect(suggestionHint("transfer")).toBe("looks like: Moved between accounts");
  });

  it("highlights the spending flow suggestion", () => {
    expect(suggestionHint("spending")).toBe("looks like: Spent it");
  });

  it("returns empty string for an unknown flow rather than a raw token", () => {
    expect(suggestionHint("something_new")).toBe("");
  });

  it("returns empty string for null/undefined", () => {
    expect(suggestionHint(null)).toBe("");
    expect(suggestionHint(undefined)).toBe("");
  });
});

describe("coverageNote", () => {
  it("states the coverage start date and the SimpleFIN 90-day cap", () => {
    expect(coverageNote("2026-04-25")).toBe(
      "Bank data starts Apr 25. SimpleFIN keeps 90 days, so nothing before that exists."
    );
  });

  it("renders nothing when there is no coverage yet", () => {
    expect(coverageNote(null)).toBe("");
  });
});

describe("weekCaption", () => {
  it("shows the week range, total, and partial-week suffix", () => {
    expect(weekCaption({ week_start: "2026-07-13", spending: 412.5, partial: true })).toBe(
      "Jul 13–19 · $412.50 · partial week"
    );
  });

  it("omits the suffix for a complete week", () => {
    expect(weekCaption({ week_start: "2026-07-13", spending: 412.5, partial: false })).toBe(
      "Jul 13–19 · $412.50"
    );
  });

  it("renders a real zero spending total, not an empty string", () => {
    expect(weekCaption({ week_start: "2026-07-13", spending: 0, partial: false })).toBe(
      "Jul 13–19 · $0"
    );
  });

  it("formats a negative (refund-net) week with a U+2212 minus before the $", () => {
    expect(weekCaption({ week_start: "2026-07-13", spending: -12.5, partial: false })).toBe(
      "Jul 13–19 · −$12.50"
    );
  });
});

describe("signedMoney", () => {
  it("formats a positive amount like money()", () => {
    expect(signedMoney(12.5)).toBe("$12.50");
  });

  it("renders a real zero, not an empty string", () => {
    expect(signedMoney(0)).toBe("$0");
  });

  it("formats a negative amount with a U+2212 minus before the $, not money()'s $-", () => {
    expect(signedMoney(-12.5)).toBe("−$12.50");
  });
});

describe("signedPct", () => {
  it("formats gains with + and losses with U+2212", () => {
    expect(signedPct(25)).toBe("+25%");
    expect(signedPct(3.6)).toBe("+3.6%");
    expect(signedPct(-5)).toBe("−5%");
    expect(signedPct(0)).toBe("+0%");
  });
});

describe("trackedShareSentence", () => {
  it("states the tracked share of total bank spending", () => {
    expect(trackedShareSentence(120, 3400)).toBe(
      "Delivery, rides, social and dates are $120 of the $3,400 above."
    );
  });

  it("renders a real zero tracked amount, not an empty string", () => {
    expect(trackedShareSentence(0, 3400)).toBe(
      "Delivery, rides, social and dates are $0 of the $3,400 above."
    );
  });

  it("returns nothing when there is no spend to be a share of", () => {
    expect(trackedShareSentence(0, 0)).toBe("");
  });
});

const line = (vendor: string, amount: number): VendorLine =>
  ({ vendor, count: 1, amount });

describe("vendorSplit", () => {
  it("returns everything in top when at or under the cutoff", () => {
    const lines = [line("A", 30), line("B", 20)];
    const { top, tail, rest } = vendorSplit(lines, 15);
    expect(top).toEqual(lines);
    expect(tail).toBeNull();
    expect(rest).toEqual([]);
  });

  it("promotes a single-vendor tail instead of an 'Everything else (1)'", () => {
    const lines = [line("A", 30), line("B", 20), line("C", 10)];
    const { top, tail } = vendorSplit(lines, 2);
    expect(top).toEqual(lines);
    expect(tail).toBeNull();
  });

  it("aggregates a multi-vendor tail and exposes its lines as rest", () => {
    const lines = [line("A", 30), line("B", 20), line("C", 10), line("D", 5)];
    const { top, tail, rest } = vendorSplit(lines, 2);
    expect(top).toEqual([line("A", 30), line("B", 20)]);
    expect(tail).toEqual({ vendors: 2, count: 2, amount: 15 });
    expect(rest).toEqual([line("C", 10), line("D", 5)]);
  });
});

interface StubEvent { gcal_event_id: string; start_at: string; title: string }
const ev = (id: string, start_at: string, title = id): StubEvent => ({ gcal_event_id: id, start_at, title });

describe("mergeRemovedSocialEvents", () => {
  it("returns the fetched list unchanged when nothing is removed", () => {
    const fetched = [ev("a", "2026-07-20T10:00:00"), ev("b", "2026-07-20T18:00:00")];
    expect(mergeRemovedSocialEvents(fetched, {})).toEqual(fetched);
  });

  it("re-inserts a removed event the backend no longer returns, sorted by start time", () => {
    const fetched = [ev("a", "2026-07-20T10:00:00")];
    const removed = { b: ev("b", "2026-07-20T08:00:00") };
    expect(mergeRemovedSocialEvents(fetched, removed)).toEqual([
      ev("b", "2026-07-20T08:00:00"),
      ev("a", "2026-07-20T10:00:00"),
    ]);
  });

  it("does not duplicate an event that is both fetched and marked removed", () => {
    // Can happen right after Undo, before the refresh() PATCH result lands —
    // the fetched copy (now restored) must win over the stale removed one.
    const fetched = [ev("a", "2026-07-20T10:00:00")];
    const removed = { a: ev("a", "2026-07-20T10:00:00") };
    expect(mergeRemovedSocialEvents(fetched, removed)).toEqual(fetched);
  });

  it("sorts multiple restored-in-place removals among fetched events", () => {
    const fetched = [ev("a", "2026-07-20T09:00:00"), ev("c", "2026-07-20T15:00:00")];
    const removed = { b: ev("b", "2026-07-20T12:00:00"), d: ev("d", "2026-07-20T18:00:00") };
    expect(mergeRemovedSocialEvents(fetched, removed).map((e) => e.gcal_event_id)).toEqual([
      "a", "b", "c", "d",
    ]);
  });
});

describe("googleAuthBroken", () => {
  it("flags a gmail auth error", () => {
    expect(googleAuthBroken("error: auth", "ok")).toBe(true);
  });

  it("flags a calendar auth error", () => {
    expect(googleAuthBroken("ok", "error: auth")).toBe(true);
  });

  it("does not flag a healthy ok status", () => {
    expect(googleAuthBroken("ok", "ok")).toBe(false);
  });

  it("does not flag when Google was never configured", () => {
    expect(googleAuthBroken("error: Google not configured", "error: Google not configured")).toBe(false);
  });

  it("does not flag other transient error kinds", () => {
    expect(googleAuthBroken("error: unreachable", "error: rate limited")).toBe(false);
    expect(googleAuthBroken("error: see logs", null)).toBe(false);
  });

  it("does not flag null statuses (job hasn't run yet)", () => {
    expect(googleAuthBroken(null, null)).toBe(false);
  });
});

import {
  categoryForKind, categoryGlyph, dayLogRowMeta, dayLogTimeLabel, isDimmed, orderDayLog,
  presentCategories, type DayLogCategory,
} from "./lib";

describe("categoryForKind", () => {
  it("maps every known row kind to its category (spec §5)", () => {
    expect(categoryForKind("delivery")).toBe("food");
    expect(categoryForKind("ride")).toBe("transport");
    expect(categoryForKind("social")).toBe("social");
    expect(categoryForKind("alcohol")).toBe("drink");
    expect(categoryForKind("gym")).toBe("fitness");
  });

  it("degrades an unrecognized kind to money rather than throwing", () => {
    expect(categoryForKind("bank")).toBe("money");
    expect(categoryForKind("")).toBe("money");
  });
});

describe("categoryGlyph", () => {
  it("returns a distinct glyph for each of the six categories", () => {
    const categories: DayLogCategory[] = ["food", "transport", "social", "drink", "fitness", "money"];
    const glyphs = categories.map(categoryGlyph);
    expect(new Set(glyphs).size).toBe(6);
  });
});

describe("dayLogTimeLabel", () => {
  it("formats a valid timestamp", () => {
    expect(dayLogTimeLabel("2026-07-24T19:37:00")).toBe("7:37 PM");
  });

  it("degrades an unparsable timestamp to an empty string", () => {
    expect(dayLogTimeLabel("not-a-date")).toBe("");
  });
});

describe("dayLogRowMeta", () => {
  it("joins time and amount, time first (spec §6 — fixes the old $ · time order)", () => {
    expect(dayLogRowMeta("2026-07-24T19:37:00", 20.93)).toBe("7:37 PM · $20.93");
  });

  it("shows only the time when there is no amount", () => {
    expect(dayLogRowMeta("2026-07-24T19:37:00", null)).toBe("7:37 PM");
  });

  it("shows only the amount when there is no time (a check-in-like row)", () => {
    expect(dayLogRowMeta(null, 18.85)).toBe("$18.85");
  });

  it("is empty when both are absent", () => {
    expect(dayLogRowMeta(null, null)).toBe("");
  });

  it("trims a whole-dollar amount the same way money() does", () => {
    expect(dayLogRowMeta(null, 20)).toBe("$20");
  });
});

interface Orderable { key: string; timeIso: string | null }
const item = (key: string, timeIso: string | null): Orderable => ({ key, timeIso });

describe("orderDayLog", () => {
  it("sorts timed rows chronologically", () => {
    const items = [item("evening", "2026-07-24T19:37:00"), item("morning", "2026-07-24T10:17:00")];
    expect(orderDayLog(items).map((i) => i.key)).toEqual(["morning", "evening"]);
  });

  it("places untimed check-in rows after every timed row (spec §1/§6)", () => {
    const items = [
      item("checkin", null),
      item("evening", "2026-07-24T19:37:00"),
      item("morning", "2026-07-24T10:17:00"),
    ];
    expect(orderDayLog(items).map((i) => i.key)).toEqual(["morning", "evening", "checkin"]);
  });

  it("preserves input order among multiple untimed rows", () => {
    const items = [item("gym", null), item("alcohol", null)];
    expect(orderDayLog(items).map((i) => i.key)).toEqual(["gym", "alcohol"]);
  });

  it("is a no-op on an all-timed, already-sorted list", () => {
    const items = [item("a", "2026-07-24T08:00:00"), item("b", "2026-07-24T09:00:00")];
    expect(orderDayLog(items).map((i) => i.key)).toEqual(["a", "b"]);
  });
});

interface Categorized { category: DayLogCategory }
const cat = (category: DayLogCategory): Categorized => ({ category });

describe("presentCategories", () => {
  it("dedupes and returns the canonical six-category order regardless of input order", () => {
    const items = [cat("drink"), cat("food"), cat("food"), cat("transport")];
    expect(presentCategories(items)).toEqual(["food", "transport", "drink"]);
  });

  it("returns an empty list for an empty day", () => {
    expect(presentCategories([])).toEqual([]);
  });

  it("never returns more than the categories actually present", () => {
    expect(presentCategories([cat("money")])).toEqual(["money"]);
  });
});

describe("isDimmed", () => {
  it("dims nothing when no filter is active", () => {
    expect(isDimmed("food", null)).toBe(false);
    expect(isDimmed("transport", null)).toBe(false);
  });

  it("dims every category except the active one", () => {
    expect(isDimmed("food", "transport")).toBe(true);
    expect(isDimmed("transport", "transport")).toBe(false);
  });
});

import { nudgeLabel, nudgeOptions } from "./lib";

describe("nudgeOptions", () => {
  it("offers auto±1 minus the current day, no future", () => {
    // Row on its automatic day: both neighbors offered, none in the future.
    expect(nudgeOptions("2026-07-29", "2026-07-29", "2026-07-30"))
      .toEqual(["2026-07-28", "2026-07-30"]);
    // Same, but "tomorrow" would be the future — only the previous day offered.
    expect(nudgeOptions("2026-07-30", "2026-07-30", "2026-07-30"))
      .toEqual(["2026-07-29"]);
    // Row already nudged forward: moving back (to auto-1 or auto) offered.
    expect(nudgeOptions("2026-07-29", "2026-07-30", "2026-07-30"))
      .toEqual(["2026-07-28", "2026-07-29"]);
  });
});

describe("nudgeLabel", () => {
  it("formats a short month-day", () => {
    expect(nudgeLabel("2026-07-29")).toBe("Jul 29");
  });
});

describe("buildSocialPatch date/location", () => {
  const base = {
    loadedTitle: "Dinner", loadedIsSocial: true, loadedAmount: null,
    loadedIsDate: false, loadedLocation: null,
    title: "Dinner", isSocial: true, amountText: "",
    isDate: false, locationText: "",
  };
  it("emits is_date only when changed", () => {
    expect(buildSocialPatch({ ...base, isDate: true })).toEqual({ is_date: true });
    expect(buildSocialPatch(base)).toEqual({});
  });
  it("emits location only when changed, empty clears to null", () => {
    expect(buildSocialPatch({ ...base, locationText: "Bar Dough" })).toEqual({ location: "Bar Dough" });
    expect(buildSocialPatch({ ...base, loadedLocation: "Bar Dough", locationText: "" }))
      .toEqual({ location: null });
    expect(buildSocialPatch({ ...base, loadedLocation: "Bar Dough", locationText: "Bar Dough" }))
      .toEqual({});
  });
});

describe("dates in day subtotals and week chips", () => {
  it("splits date spend out of Social in Spent today", () => {
    const rows = subtotalsFromDay({
      deliveries: [],
      rides: [],
      social_events: [
        { amount: 20, end_at: "2026-07-15T21:00:00", is_date: false },
        { amount: 60, end_at: "2026-07-15T22:00:00", is_date: true },
      ],
    }, new Date("2026-07-16T12:00:00").getTime());
    expect(rows).toEqual([
      { kind: "date", service: "Dates", amount: 60 },
      { kind: "social", service: "Social", amount: 20 },
    ]);
  });

  it("gives a date-only day a chip so WeekDays never shows 'Work travel'", () => {
    const day: Day = {
      date: "2026-07-15", gym: false, alcohol_level: null, substances: false,
      total: 60,
      items: [{ kind: "date", service: "Dates", label: "Date night", at: "x", amount: 60, is_work: false }],
    };
    expect(dayChips(day)).toEqual([{ label: "Date", tone: "accent" }]);
  });
});
