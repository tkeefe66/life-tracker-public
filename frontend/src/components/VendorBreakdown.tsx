import { useEffect, useRef, useState } from "react";
import { apiGet, apiSend } from "../api";
import { dayRowDate, flowLabel, money, signedMoney, vendorSplit, type VendorLine } from "../lib";

interface LabelLine { label: string | null; count: number; amount: number; suggested?: number }
interface AccountRow { id: number; name: string; display_name: string; role: string; active: boolean }
interface DrillRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  account_name: string;
  resolved_flow: string;
  user_note: string | null;
  user_label: string | null;
  suggested_label: string | null;
  user_no_label: boolean;
  vendor: string;
}

interface LabelSaveResponse { ok: boolean; label: string | null; siblings: number; vendor: string }

interface BulkOffer {
  drillKey: string;
  simplefin_id: string;
  vendor: string;
  label: string;
  siblings: number;
}

// "Where it went" — bank spending grouped by vendor, filterable by account,
// with a per-vendor transaction drill-down. Secondary surface: the section
// is absent only before the first unfiltered fetch resolves, or when that
// fetch shows genuinely no bank spending. Once spending data is known to exist,
// a failed or in-flight per-view (payee/label) fetch keeps the mode/account chips
// mounted and simply shows nothing where the list would be — it never unmounts
// the whole section and never falsely claims the window is empty.
export default function VendorBreakdown({ weeks }: { weeks: number }) {
  const [lines, setLines] = useState<VendorLine[] | null>(null);
  const [mode, setMode] = useState<"payee" | "label">("payee");
  const [labelLines, setLabelLines] = useState<LabelLine[] | null>(null);
  // Whether the last unfiltered (All accounts) fetch came back empty — the
  // ONLY signal allowed to hide the whole section. A filtered or per-mode
  // empty result must keep the chips rendered (there'd be no way to deselect).
  const [unfilteredEmpty, setUnfilteredEmpty] = useState<boolean | null>(null);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [showRest, setShowRest] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [drill, setDrill] = useState<Record<string, DrillRow[]>>({});
  const [vocab, setVocab] = useState<string[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [offer, setOffer] = useState<BulkOffer | null>(null);
  // Set right before the Escape-triggered setEditing(null) so the input's
  // onBlur (fired by React unmounting the now-unfocused input) can tell a
  // deliberate cancel apart from a normal blur-to-save and skip saveLabel.
  const cancelingRef = useRef(false);
  // Bumped on every accountId/weeks change; fetch closures capture the value
  // at call time and discard their response if it no longer matches, so a
  // stale in-flight lines or drill fetch can never repopulate state after a
  // newer request has superseded it.
  const fetchGen = useRef(0);

  useEffect(() => {
    apiGet<AccountRow[]>("/bank/accounts")
      // investment-role accounts get no chip: by construction (bank_flows
      // classify_flow rule 1) their rows are never spending/refund, so the
      // chip could only ever show an empty result.
      .then((rows) => setAccounts(rows.filter((a) => a.active && a.role !== "investment")))
      .catch(() => setAccounts([]));
  }, []);

  useEffect(() => {
    const acct = accountId !== null ? `&account_id=${accountId}` : "";
    setExpanded(null);
    setDrill({});
    setShowRest(false);
    setOffer(null);
    fetchGen.current += 1;
    const gen = fetchGen.current;
    const byParam = mode === "label" ? "&by=label" : "";
    apiGet<{ lines: (VendorLine | LabelLine)[]; labels: string[] }>(
      `/bank/breakdown?weeks=${weeks}${byParam}${acct}`,
    )
      .then((d) => {
        if (fetchGen.current !== gen) return;
        setVocab(d.labels ?? []);
        if (accountId === null) setUnfilteredEmpty(d.lines.length === 0);
        if (mode === "label") setLabelLines(d.lines as LabelLine[]);
        else setLines(d.lines as VendorLine[]);
      })
      .catch(() => {
        if (fetchGen.current !== gen) return;
        // A failed unfiltered refetch demotes confirmed-empty to unknown —
        // never the reverse (false must survive, or an established section
        // would vanish on a transient failure).
        if (accountId === null) setUnfilteredEmpty((v) => (v === true ? null : v));
        if (mode === "label") setLabelLines(null);
        else setLines(null);
      });
  }, [weeks, accountId, mode]);

  const saveLabel = (drillKey: string, row: DrillRow, raw: string) => {
    const label = raw.trim() || null;
    setEditing(null);
    if (label === row.user_label) return;
    const gen = fetchGen.current;
    apiSend<LabelSaveResponse>("POST", "/bank/label", { simplefin_id: row.simplefin_id, label })
      .then((resp) => {
        if (fetchGen.current !== gen) return;
        setDrill((prev) => ({
          ...prev,
          [drillKey]: (prev[drillKey] ?? []).map((r) =>
            r.simplefin_id === row.simplefin_id ? { ...r, user_label: label } : r),
        }));
        if (label && !vocab.includes(label)) {
          setVocab((v) => [...v, label].sort());
        }
        setOffer((o) =>
          o && (o.simplefin_id === row.simplefin_id || o.vendor === resp.vendor) ? null : o,
        );
        if (label && resp.siblings > 0) {
          setOffer({
            drillKey,
            simplefin_id: row.simplefin_id,
            vendor: resp.vendor,
            label,
            siblings: resp.siblings,
          });
        }
      })
      .catch(() => {});
  };

  const applyBulk = () => {
    if (!offer) return;
    const { drillKey, vendor, label } = offer;
    setOffer(null);
    const gen = fetchGen.current;
    apiSend("POST", "/bank/label", { payee: vendor, label })
      .then(() => {
        if (fetchGen.current !== gen) return;
        setDrill((prev) => ({
          ...prev,
          [drillKey]: (prev[drillKey] ?? []).map((r) =>
            r.vendor === vendor && !r.user_label ? { ...r, user_label: label } : r),
        }));
      })
      .catch(() => {});
  };

  const view = mode === "payee" ? lines : labelLines;
  // Nothing known yet (first load in flight, or the initial fetch failed):
  // no section at all — the original secondary-surface behavior.
  if (unfilteredEmpty === null && view === null) return null;
  // Genuinely no bank spending in the window: no section.
  if (unfilteredEmpty) return null;

  const { top, tail, rest } = vendorSplit(lines ?? []);
  const shown = showRest ? [...top, ...rest] : top;
  // Current view's data state: null = loading or failed (render the chips,
  // no list, and NO false empty-state line), [] = a real empty result.
  const viewEmpty = view !== null && view.length === 0;

  // Generic drill toggle shared by both views: `key` is the vendor name in
  // payee mode or "label:"+label in label mode; `fetchQuery` is the
  // pre-built payee=/label= query fragment for the rows endpoint.
  const toggleDrill = (key: string, fetchQuery: string) => {
    if (expanded === key) {
      setExpanded(null);
      setOffer(null);
      return;
    }
    setExpanded(key);
    setOffer((o) => (o && o.drillKey !== key ? null : o));
    if (!drill[key]) {
      const acct = accountId !== null ? `&account_id=${accountId}` : "";
      const gen = fetchGen.current;
      apiGet<{ rows: DrillRow[] }>(
        `/bank/breakdown/rows?weeks=${weeks}&${fetchQuery}${acct}`,
      )
        .then((d) => {
          if (fetchGen.current !== gen) return;
          setDrill((prev) => ({ ...prev, [key]: d.rows }));
        })
        .catch(() => {});
    }
  };

  // Row + drill-down JSX shared by the vendor and label views. `drillKey`
  // keys `expanded`/`drill` state; `fetchQuery` is only used (and only
  // needs to be valid) when `expandable` is true.
  const renderRow = (
    displayName: string,
    drillKey: string,
    expandable: boolean,
    fetchQuery: string,
    count: number,
    amount: number,
    i: number,
    suggested = 0,
  ) => {
    const drillId = `vendor-drill-${mode}-${i}`;
    const isOpen = expandable && expanded === drillKey;
    return (
      <div key={drillKey}>
        <button
          type="button"
          className="vendor-row"
          aria-expanded={expandable ? isOpen : undefined}
          aria-controls={expandable ? drillId : undefined}
          onClick={expandable ? () => toggleDrill(drillKey, fetchQuery) : undefined}
        >
          <span className="spend-service">
            {displayName}
            {mode === "label" && suggested > 0 && (
              <span className="suggest-badge">{suggested} suggested</span>
            )}
          </span>
          <span className="spend-amount num">
            {count > 0 ? `${count} · ` : ""}{signedMoney(amount)}
          </span>
        </button>
        {isOpen && drill[drillKey] && (
          <div className="vendor-drill" id={drillId}>
            {drill[drillKey].map((r) => {
              const shownLabel = r.user_label ?? (r.user_no_label ? null : r.suggested_label);
              return (
                <div key={r.simplefin_id}>
                  <div className="vendor-drill-row">
                    <span>
                      {dayRowDate(r.posted.slice(0, 10)).monthDay}
                      {r.resolved_flow === "refund" && ` · ${flowLabel("refund")}`}
                      {mode === "label" && ` · ${r.vendor}`}
                      {" · "}{r.account_name}
                      {r.user_note && <span className="triage-note">{r.user_note}</span>}
                    </span>
                    <span className="vendor-drill-right">
                      {editing === r.simplefin_id ? (
                        <input
                          className="vendor-label-input"
                          list="vendor-label-vocab"
                          aria-label="Label"
                          defaultValue={r.user_label ?? (r.user_no_label ? null : r.suggested_label) ?? ""}
                          autoFocus
                          onBlur={(e) => {
                            if (cancelingRef.current) {
                              cancelingRef.current = false;
                              return;
                            }
                            saveLabel(drillKey, r, e.currentTarget.value);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") e.currentTarget.blur();
                            if (e.key === "Escape") {
                              cancelingRef.current = true;
                              setEditing(null);
                            }
                          }}
                        />
                      ) : (
                        <button
                          type="button"
                          className={
                            !r.user_label && r.suggested_label && !r.user_no_label
                              ? "vendor-label-btn vendor-label-suggested"
                              : "vendor-label-btn"
                          }
                          onClick={() => setEditing(r.simplefin_id)}
                        >
                          {shownLabel ?? "＋ label"}
                        </button>
                      )}
                      <span className="num">{money(Math.abs(r.amount))}</span>
                    </span>
                  </div>
                  {offer && offer.drillKey === drillKey && offer.simplefin_id === r.simplefin_id && (
                    <button type="button" className="vendor-bulk-offer" onClick={() => applyBulk()}>
                      Apply "{offer.label}" to {offer.siblings} more {offer.vendor} row{offer.siblings === 1 ? "" : "s"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      <h2 className="section-label">Where it went</h2>
      <datalist id="vendor-label-vocab">
        {vocab.map((l) => <option key={l} value={l} />)}
      </datalist>
      <div className="vendor-chip-row">
        <button
          type="button"
          className={mode === "payee" ? "vendor-chip vendor-chip-on" : "vendor-chip"}
          onClick={() => setMode("payee")}
        >
          Vendors
        </button>
        <button
          type="button"
          className={mode === "label" ? "vendor-chip vendor-chip-on" : "vendor-chip"}
          onClick={() => setMode("label")}
        >
          Labels
        </button>
      </div>
      {accounts.length > 1 && (
        <div className="vendor-chip-row">
          <button
            type="button"
            className={accountId === null ? "vendor-chip vendor-chip-on" : "vendor-chip"}
            onClick={() => setAccountId(null)}
          >
            All accounts
          </button>
          {accounts.map((a) => (
            <button
              key={a.id}
              type="button"
              className={accountId === a.id ? "vendor-chip vendor-chip-on" : "vendor-chip"}
              onClick={() => setAccountId((prev) => (prev === a.id ? null : a.id))}
            >
              {a.display_name}
            </button>
          ))}
        </div>
      )}
      {view === null ? null : viewEmpty ? (
        <p className="quiet">No spending in this account over this window.</p>
      ) : mode === "payee" ? (
        <div className="spend">
          {shown.map((l, i) =>
            renderRow(
              l.vendor,
              l.vendor,
              true,
              `payee=${encodeURIComponent(l.vendor)}`,
              l.count,
              l.amount,
              i,
            ),
          )}
          {tail && !showRest && (
            <button
              type="button"
              className="vendor-row"
              onClick={() => setShowRest(true)}
            >
              <span className="spend-service">Everything else ({tail.vendors} vendors)</span>
              <span className="spend-amount num">{tail.count} · {signedMoney(tail.amount)}</span>
            </button>
          )}
        </div>
      ) : (
        <div className="spend">
          {(labelLines ?? []).map((l, i) => {
            const displayName = l.label ?? "Unlabeled";
            const drillKey = "label:" + l.label;
            const expandable = l.label !== null;
            const fetchQuery = expandable ? `label=${encodeURIComponent(l.label as string)}` : "";
            return renderRow(
              displayName, drillKey, expandable, fetchQuery, l.count, l.amount, i, l.suggested,
            );
          })}
        </div>
      )}
    </>
  );
}
