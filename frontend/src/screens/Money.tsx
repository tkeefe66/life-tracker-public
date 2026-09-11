import { useEffect, useRef, useState } from "react";
import { apiGet, apiSend } from "../api";
import {
  coverageNote, flowLabel, money, trackedShareSentence, weekCaption, weekRangeLabel,
  TRIAGE_CHOICES, type SpendRow, type WeekSpendPoint,
} from "../lib";
import BankSpendChart from "../components/BankSpendChart";
import Investments from "../components/Investments";
import LabelAudit from "../components/LabelAudit";
import SpendChart, { type SpendWeekPoint } from "../components/SpendChart";
import SpendSubtotals from "../components/SpendSubtotals";
import TriageQueue, { type TriageRow } from "../components/TriageQueue";
import VendorBreakdown from "../components/VendorBreakdown";

interface BankFlowTotal { count: number; amount: number }
interface BankSummaryData {
  covered_from: string | null;
  covered_to: string | null;
  weeks: WeekSpendPoint[];
  totals: Record<string, BankFlowTotal>;
  spent: number;
  tracked: SpendRow[];
  triage_counts: { ambiguous: number; inflow_unknown: number };
}
interface BankTriageData {
  ambiguous: TriageRow[];
  inflow_unknown: TriageRow[];
  recent: TriageRow[];
}
interface MoneySettings { bank_last_status: string | null; bank_last_run: string | null }
interface TrackedSpendData { weeks: SpendWeekPoint[] }

const MOVEMENT_FLOWS: { flow: string }[] = [
  { flow: "card_payment" },
  { flow: "transfer" },
  { flow: "investment" },
];

const LEGEND: { key: "delivery" | "rides" | "social" | "dates"; label: string; token: string }[] = [
  { key: "delivery", label: "Delivery", token: "var(--chart-delivery)" },
  { key: "rides", label: "Rides", token: "var(--chart-rides)" },
  { key: "social", label: "Social", token: "var(--chart-social)" },
  { key: "dates", label: "Dates", token: "var(--chart-dates)" },
];

// How long an answered row lingers in its queue before actually being removed.
// TriageQueue's one-shot bulk offer ("Apply to the other N…") renders directly
// under the row that was just answered, for as long as that row is still in
// the `rows` array (see TriageQueue's own doc comment). Removing the row in
// the exact same tick as answering it — which "optimistic, immediate removal"
// would literally mean — would make the bulk offer invisible for its entire
// life. This linger is what makes §6.3's bulk correction usable at all: the
// network request still fires immediately in the background (truly
// optimistic — nothing waits on it), only the row's on-screen removal is
// deferred a few seconds so the offer has a real window to be tapped.
const ANSWERED_LINGER_MS = 4000;

function bankStatusLine(status: string | null, run: string | null): string {
  if (!status) return "Hasn't run yet";
  const when = run ? new Date(run) : null;
  const at = when && !isNaN(when.getTime())
    ? when.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "";
  return status === "ok" ? `OK${at ? ` · ${at}` : ""}` : status;
}

export default function Money() {
  const [summary, setSummary] = useState<BankSummaryData | null>(null);
  const [settings, setSettings] = useState<MoneySettings | null>(null);
  const [trackedSpend, setTrackedSpend] = useState<TrackedSpendData | null>(null);
  const [ambiguousRows, setAmbiguousRows] = useState<TriageRow[] | null>(null);
  const [inflowRows, setInflowRows] = useState<TriageRow[] | null>(null);
  const [recentRows, setRecentRows] = useState<TriageRow[] | null>(null);
  // Lifted from LabelAudit via its onTotal callback — no second fetch, see
  // that component's doc comment.
  const [labelAuditTotal, setLabelAuditTotal] = useState(0);
  // Controlled <details> open state for the decision zone, tracked so the
  // zone can stay visible when the user answers its last item while it's
  // open (see decisionZoneVisible below) rather than yanking it away
  // mid-interaction.
  const [decisionZoneOpen, setDecisionZoneOpen] = useState(false);

  const [selectedBankWeek, setSelectedBankWeek] = useState<number | null>(null);
  const [selectedTrackedWeek, setSelectedTrackedWeek] = useState<number | null>(null);

  const timersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    apiGet<BankSummaryData>("/bank/summary?weeks=12").then(setSummary).catch(() => setSummary(null));
  }, []);

  useEffect(() => {
    apiGet<MoneySettings>("/settings").then(setSettings).catch(() => setSettings(null));
  }, []);

  useEffect(() => {
    apiGet<TrackedSpendData>("/spend?weeks=12").then(setTrackedSpend).catch(() => setTrackedSpend(null));
  }, []);

  useEffect(() => {
    apiGet<BankTriageData>("/bank/triage?limit=50")
      .then((d) => {
        setAmbiguousRows(d.ambiguous);
        setInflowRows(d.inflow_unknown);
        setRecentRows(d.recent);
      })
      .catch(() => {
        setAmbiguousRows(null);
        setInflowRows(null);
        setRecentRows(null);
      });
  }, []);

  // Clear any pending linger timers on unmount so a stale setState never fires
  // after the screen has gone away.
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      Object.values(timers).forEach(clearTimeout);
    };
  }, []);

  const reloadSummary = () => {
    apiGet<BankSummaryData>("/bank/summary?weeks=12").then(setSummary).catch(() => {});
  };

  const scheduleRemoval = (
    id: string,
    setRows: React.Dispatch<React.SetStateAction<TriageRow[] | null>>,
  ) => {
    clearTimeout(timersRef.current[id]);
    timersRef.current[id] = setTimeout(() => {
      setRows((prev) => prev?.filter((r) => r.simplefin_id !== id) ?? prev);
      delete timersRef.current[id];
    }, ANSWERED_LINGER_MS);
  };

  const cancelRemoval = (id: string) => {
    clearTimeout(timersRef.current[id]);
    delete timersRef.current[id];
  };

  // Build the "recently sorted" representation of a row that was just
  // answered — resolved_flow/user_flow follow the same COALESCE(user_flow,
  // flow) resolution the server does, just computed here since we already
  // hold the row and don't want to wait on a refetch to show it. `note`,
  // when provided, is the just-typed text that rides along with this
  // answer; otherwise the row keeps whatever user_note it already had.
  const toRecentRow = (row: TriageRow, flow: string, note?: string): TriageRow => ({
    ...row,
    user_flow: flow,
    resolved_flow: flow,
    user_note: note !== undefined ? note : row.user_note,
  });

  const addToRecent = (rows: TriageRow[]) => {
    setRecentRows((prev) => {
      const ids = new Set(rows.map((r) => r.simplefin_id));
      const withoutDup = (prev ?? []).filter((r) => !ids.has(r.simplefin_id));
      return [...rows, ...withoutDup];
    });
  };

  const handleAnswer = (bucket: "ambiguous" | "inflow", row: TriageRow, flow: string, note?: string) => {
    // A second tap on the same row while it's still lingering from the first
    // answer — the earlier POST is in flight (or already resolved) and this
    // one would race it for which flow wins. Mirror handleBulk's guard: if
    // the row already has a pending removal timer, it's already been
    // answered, so make the second tap inert.
    if (timersRef.current[row.simplefin_id]) return;
    const setRows = bucket === "ambiguous" ? setAmbiguousRows : setInflowRows;
    // Fires now, in the background — the request never waits on anything above.
    scheduleRemoval(row.simplefin_id, setRows);
    // `note` only enters the body when defined — omitted means "don't touch"
    // server-side; it must never be sent as an explicit null or empty string.
    const body: { flow: string; note?: string } = note !== undefined ? { flow, note } : { flow };
    apiSend("POST", `/bank/transactions/${row.simplefin_id}/flow`, body)
      .then(() => {
        reloadSummary();
        addToRecent([toRecentRow(row, flow, note)]);
      })
      .catch(() => {
        cancelRemoval(row.simplefin_id);
        setRows((prev) => (prev && !prev.some((r) => r.simplefin_id === row.simplefin_id)
          ? [row, ...prev]
          : prev));
      });
  };

  const handleBulk = (bucket: "ambiguous" | "inflow", ids: string[], flow: string) => {
    const rows = bucket === "ambiguous" ? ambiguousRows : inflowRows;
    const setRows = bucket === "ambiguous" ? setAmbiguousRows : setInflowRows;
    const removed = (rows ?? []).filter((r) => ids.includes(r.simplefin_id));
    // Nothing left matching these ids — either already applied by an earlier
    // click, or the state just hasn't caught up yet. Either way, do nothing:
    // this is the guard against a double-fire the child button doesn't have.
    if (removed.length === 0) return;
    removed.forEach((r) => cancelRemoval(r.simplefin_id));
    setRows((prev) => prev?.filter((r) => !ids.includes(r.simplefin_id)) ?? prev);
    apiSend("POST", "/bank/transactions/flow", { simplefin_ids: ids, flow })
      .then(() => {
        reloadSummary();
        addToRecent(removed.map((r) => toRecentRow(r, flow)));
      })
      .catch(() => {
        setRows((prev) => (prev ? [...removed, ...prev] : prev));
      });
  };

  const handlePutBack = (row: TriageRow) => {
    setRecentRows((prev) => prev?.filter((r) => r.simplefin_id !== row.simplefin_id) ?? prev);
    apiSend("POST", `/bank/transactions/${row.simplefin_id}/flow`, { flow: null })
      .then(() => {
        reloadSummary();
        // Server-side the row is back to user_flow = NULL, so a refetch is
        // what routes it back to its correct queue — the client can't know
        // which bucket it belongs to (rows don't carry the `ambiguous` flag).
        // Merge rather than replace outright: any row still mid-linger from
        // an unrelated answer shouldn't duplicate or resurrect just because
        // the refetch's snapshot predates its removal timer firing. This
        // refetch has its own quiet catch — the put-back POST above already
        // succeeded, so a failure here must never trigger the "put it back
        // failed" recovery below.
        apiGet<BankTriageData>("/bank/triage?limit=50")
          .then((d) => {
            // Accepted tradeoff: if this refetch lands while another row is still
            // lingering (mid ANSWERED_LINGER_MS or its bulk-offer window), excluding
            // it here ends that row's on-screen linger/bulk window early. It only
            // shortens the window — the row's persisted state is untouched, and its
            // own pending removal timer still fires on schedule — so it never
            // corrupts state, just occasionally cuts a bulk-offer's visible time short.
            const lingering = new Set(Object.keys(timersRef.current));
            setAmbiguousRows(d.ambiguous.filter((r) => !lingering.has(r.simplefin_id)));
            setInflowRows(d.inflow_unknown.filter((r) => !lingering.has(r.simplefin_id)));
            setRecentRows(d.recent);
          })
          .catch(() => {});
      })
      .catch(() => setRecentRows((prev) => (prev ? [row, ...prev] : prev)));
  };

  const notConfigured = settings?.bank_last_status === "error: not configured";
  const awaitingFirstSync = !!summary && !notConfigured && summary.covered_from === null;
  const bankSectionsVisible = !!summary && !notConfigured && !awaitingFirstSync;

  const selectedBankPoint = bankSectionsVisible && selectedBankWeek !== null
    ? summary!.weeks[selectedBankWeek] ?? null
    : null;
  const selectedTrackedPoint = trackedSpend && selectedTrackedWeek !== null
    ? trackedSpend.weeks[selectedTrackedWeek] ?? null
    : null;

  const ambiguousMore = bankSectionsVisible && ambiguousRows
    ? Math.max(0, summary!.triage_counts.ambiguous - ambiguousRows.length)
    : 0;
  const inflowMore = bankSectionsVisible && inflowRows
    ? Math.max(0, summary!.triage_counts.inflow_unknown - inflowRows.length)
    : 0;

  // n = everything the decision zone's collapsed summary counts: the two
  // triage buckets (known as soon as /bank/summary loads) plus whatever
  // LabelAudit has reported of itself so far (0 until its own fetch
  // resolves — see the mounting note below).
  const decisionZoneCount = bankSectionsVisible && summary
    ? summary.triage_counts.ambiguous + summary.triage_counts.inflow_unknown + labelAuditTotal
    : 0;
  // The zone stays visible once opened even if n drops to 0 mid-interaction
  // (spec: don't yank the section away while the user is still in it) — it
  // only goes quiet again on the next collapse or the next visit.
  const decisionZoneVisible = decisionZoneCount > 0 || decisionZoneOpen;

  const coverage = summary ? coverageNote(summary.covered_from) : "";

  const trackedTotal = summary ? summary.tracked.reduce((sum, r) => sum + r.amount, 0) : 0;
  const trackedShare = summary ? trackedShareSentence(trackedTotal, summary.spent) : "";

  return (
    <div>
      <h1 className="visually-hidden">Money</h1>
      {notConfigured && <p className="quiet empty">Bank sync isn't set up.</p>}

      {awaitingFirstSync && (
        <>
          <p className="quiet empty">Waiting for the first sync.</p>
          {settings && (
            <p className="footnote">{bankStatusLine(settings.bank_last_status, settings.bank_last_run)}</p>
          )}
        </>
      )}

      {bankSectionsVisible && summary && (
        <>
          <p className="money-hero">{money(summary.spent)}</p>
          <p className="money-hero-sub">spent · last 12 weeks</p>
          {(summary.totals.refund?.amount ?? 0) > 0 && (
            <p className="money-hero-sub">after {money(summary.totals.refund.amount)} refunded</p>
          )}

          <BankSpendChart
            weeks={summary.weeks}
            onSelect={(i) => setSelectedBankWeek((prev) => (prev === i ? null : i))}
          />
          {selectedBankPoint && (
            <p className="trend-caption">{weekCaption(selectedBankPoint)}</p>
          )}

          <VendorBreakdown weeks={12} />

          {MOVEMENT_FLOWS.some((m) => summary.totals[m.flow]) && (
            <>
              <h2 className="section-label">Where the rest went</h2>
              <div className="spend">
                {MOVEMENT_FLOWS.map(({ flow }) => {
                  const t = summary.totals[flow];
                  if (!t) return null;
                  return (
                    <div className="spend-row" key={flow}>
                      <span className="spend-service">{flowLabel(flow)}</span>
                      <span className="spend-amount num">{t.count} · {money(t.amount)}</span>
                    </div>
                  );
                })}
              </div>
              <p className="footnote">Separated out so they don't count as spending.</p>
            </>
          )}

          {summary.totals.income && (
            <>
              <h2 className="section-label">Money in</h2>
              <div className="spend">
                <div className="spend-row">
                  <span className="spend-service">at least</span>
                  <span className="spend-amount num">{money(summary.totals.income.amount)}</span>
                </div>
              </div>
              <p className="footnote">
                Only deposits matching a known payroll signature count. Anything from an account
                that isn't connected doesn't appear here.
              </p>
            </>
          )}
        </>
      )}

      {summary && (
        <>
          {trackedShare && (
            <p className="quiet">
              <span>{trackedShare}</span>
            </p>
          )}
          <SpendSubtotals
            rows={summary.tracked}
            // The containment framing ("Of that...") only makes sense once the bank
            // hero/total above it is actually on screen — and even then, the
            // tracked-share sentence just above already restates both figures, so
            // "Of that" would be a third restatement of the same relationship.
            // Fall back to the neutral heading this block had on the old Insights
            // money view when bank sections aren't visible at all.
            title={bankSectionsVisible ? "The things you're tracking" : "By service · last 12 weeks"}
          />

          {trackedSpend && trackedSpend.weeks.length > 0 && (
            <details className="money-details">
              <summary>Show tracked categories over time</summary>
              <SpendChart
                weeks={trackedSpend.weeks}
                onSelect={(i) => setSelectedTrackedWeek((prev) => (prev === i ? null : i))}
              />
              <div className="legend">
                {LEGEND.map((l) => (
                  <span className="legend-item" key={l.key}>
                    <span className="legend-swatch" style={{ background: l.token }} />
                    {l.label}
                  </span>
                ))}
              </div>
              {selectedTrackedPoint && (
                <p className="trend-caption">
                  {weekRangeLabel(selectedTrackedPoint.week_start)} · Delivery{" "}
                  {money(selectedTrackedPoint.delivery)} · Rides {money(selectedTrackedPoint.rides)} ·
                  {" "}Social {money(selectedTrackedPoint.social)} · Dates {money(selectedTrackedPoint.dates)}
                  {selectedTrackedWeek === trackedSpend.weeks.length - 1 && " · In progress"}
                </p>
              )}
            </details>
          )}
        </>
      )}

      {bankSectionsVisible && (
        // zone-break: separates the decision zone from the record zone above
        // it — one hairline, one extra margin, no new color or card (see
        // .zone-break.money-details in styles.css). The whole zone collapses
        // behind its <summary> ("N things to sort out") so the screen
        // defaults to being a record, not a worklist.
        //
        // This <details> stays mounted whenever bank sections are visible —
        // even when decisionZoneVisible is false — and uses the `hidden`
        // attribute rather than an unmounting `{visible && ...}` guard. That's
        // deliberate: LabelAudit's own count only becomes known after ITS
        // internal fetch resolves, and that fetch only runs while it's
        // mounted. Unmounting the wrapper whenever the (initially-zero)
        // count says "nothing to show" would mean it could never mount in
        // the first place if the only pending items are label suggestions —
        // a real deadlock, not just a cosmetic flash. `hidden` keeps
        // TriageQueue/LabelAudit mounted and fetching throughout, and simply
        // removes the wrapper from rendering (and the accessibility tree,
        // matching "quiet stays quiet") until decisionZoneVisible flips true.
        <details
          className="money-details zone-break"
          hidden={!decisionZoneVisible}
          open={decisionZoneOpen}
          onToggle={(e) => setDecisionZoneOpen(e.currentTarget.open)}
        >
          <summary>
            {decisionZoneCount} thing{decisionZoneCount === 1 ? "" : "s"} to sort out
          </summary>

          <h2 className="section-label">Needs a decision</h2>
          <TriageQueue
            title="Spent it, or moved it?"
            prompt="These read like transfers but were counted as spending."
            rows={ambiguousRows ?? []}
            choices={TRIAGE_CHOICES.outflow}
            onAnswer={(id, flow, note) => {
              const row = ambiguousRows?.find((r) => r.simplefin_id === id);
              if (row) handleAnswer("ambiguous", row, flow, note);
            }}
            onBulk={(ids, flow) => handleBulk("ambiguous", ids, flow)}
          />
          {ambiguousMore > 0 && <p className="triage-more">{ambiguousMore} more</p>}

          <TriageQueue
            title="Where did this come from?"
            prompt="A deposit that isn't payroll."
            rows={inflowRows ?? []}
            choices={TRIAGE_CHOICES.inflow}
            onAnswer={(id, flow, note) => {
              const row = inflowRows?.find((r) => r.simplefin_id === id);
              if (row) handleAnswer("inflow", row, flow, note);
            }}
            onBulk={(ids, flow) => handleBulk("inflow", ids, flow)}
          />
          {inflowMore > 0 && <p className="triage-more">{inflowMore} more</p>}

          {recentRows && recentRows.length > 0 && (
            <details className="money-details">
              <summary>Recently sorted</summary>
              {recentRows.map((r) => (
                <div className="recent-row" key={r.simplefin_id}>
                  <span>
                    {r.label} — {flowLabel(r.resolved_flow)}
                    {r.user_note && <span className="triage-note">{r.user_note}</span>}
                  </span>
                  <button type="button" onClick={() => handlePutBack(r)}>Put it back</button>
                </div>
              ))}
            </details>
          )}

          <LabelAudit onTotal={setLabelAuditTotal} />
        </details>
      )}

      {bankSectionsVisible && <Investments />}

      {coverage && <p className="footnote">{coverage}</p>}
    </div>
  );
}
