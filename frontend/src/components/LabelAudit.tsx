import { useEffect, useRef, useState } from "react";
import { apiGet, apiSend } from "../api";
import { dayRowDate, money } from "../lib";

interface SuggestionRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  vendor: string;
  account_name: string;
  suggested_label: string;
  description: string;
}

interface LabelAuditProps {
  // Lets the parent (Money) fold this count into the collapsed decision-zone
  // summary ("N things to sort out") without a second fetch — fired once
  // after the initial load and again on every local total change (confirm/
  // reject/restore). A failed initial fetch reports 0 so a broken audit
  // fetch can't hold the zone open by itself.
  onTotal?: (n: number) => void;
}

// "Suggested labels — needs a look": every unconfirmed suggestion, one tap
// to confirm / change / reject. Rejection is durable (no_label verdict).
// Secondary surface: failed fetch hides the section; renders null when empty.
export default function LabelAudit({ onTotal }: LabelAuditProps = {}) {
  const [rows, setRows] = useState<SuggestionRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [editing, setEditing] = useState<string | null>(null);
  // Set right before the Escape-triggered setEditing(null) so the input's
  // onBlur (fired by React unmounting the now-unfocused input) can tell a
  // deliberate cancel apart from a normal blur-to-save and skip answer().
  const cancelingRef = useRef(false);

  useEffect(() => {
    apiGet<{ rows: SuggestionRow[]; total: number }>("/bank/label-suggestions?limit=50")
      .then((d) => {
        setRows(d.rows);
        setTotal(d.total);
      })
      .catch(() => {
        setRows(null);
        onTotal?.(0);
      });
    // Only the mount matters here — onTotal is a stable setState from the
    // parent, and re-running this fetch on every render would defeat the
    // "lift the count up without a second fetch" point entirely.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Every local total change (initial fetch success included) rides out to
  // the parent here, in one place, rather than at each call site.
  useEffect(() => {
    onTotal?.(total);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [total]);

  if (!rows || rows.length === 0) return null;

  const removeRow = (r: SuggestionRow) => {
    setRows((prev) => prev?.filter((x) => x.simplefin_id !== r.simplefin_id) ?? prev);
    setTotal((t) => Math.max(0, t - 1));
  };
  const restoreRow = (r: SuggestionRow) => {
    setRows((prev) => (prev && !prev.some((x) => x.simplefin_id === r.simplefin_id)
      ? [r, ...prev] : prev));
    setTotal((t) => t + 1);
  };

  const answer = (r: SuggestionRow, body: object) => {
    removeRow(r);
    apiSend("POST", "/bank/label", body).catch(() => restoreRow(r));
  };

  return (
    <>
      <h2 className="section-label">Suggested labels — needs a look</h2>
      <p className="footnote">
        {total} transaction{total === 1 ? "" : "s"} inherited a label you haven't confirmed.
      </p>
      {rows.map((r) => (
        <div className="audit-card" key={r.simplefin_id}>
          <div className="audit-head">
            <span>{r.vendor}</span>
            <span className="num">{money(Math.abs(r.amount))}</span>
          </div>
          <p className="audit-meta">
            {dayRowDate(r.posted.slice(0, 10)).monthDay} · {r.account_name} · suggested:{" "}
            <em>{r.suggested_label}</em>
          </p>
          {editing === r.simplefin_id ? (
            <input
              className="vendor-label-input"
              defaultValue={r.suggested_label}
              autoFocus
              aria-label="Label"
              onBlur={(e) => {
                if (cancelingRef.current) {
                  cancelingRef.current = false;
                  return;
                }
                const label = e.currentTarget.value.trim();
                setEditing(null);
                if (label) answer(r, { simplefin_id: r.simplefin_id, label });
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
            <div className="audit-btns">
              <button type="button" className="audit-btn audit-btn-primary"
                onClick={() => answer(r, { simplefin_id: r.simplefin_id, label: r.suggested_label })}>
                ✓ {r.suggested_label}
              </button>
              <button type="button" className="audit-btn"
                onClick={() => setEditing(r.simplefin_id)}>
                Change…
              </button>
              <button type="button" className="audit-btn"
                onClick={() => answer(r, { simplefin_id: r.simplefin_id, no_label: true })}>
                No label
              </button>
            </div>
          )}
        </div>
      ))}
      {total > rows.length && <p className="triage-more">{total - rows.length} more</p>}
    </>
  );
}
