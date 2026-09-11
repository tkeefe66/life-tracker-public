import { useState } from "react";
import { dayRowDate, money, suggestionHint, type TriageChoice } from "../lib";

/** One row of the triage worklist (spec §6.2) — the shape `app/money.py`'s
 * `_decorate_bucket` returns for both the `ambiguous` and `inflow_unknown`
 * buckets. `amount` is signed (negative = money out, positive = money in);
 * this component always displays its magnitude — the queue's own heading
 * ("Spent it, or moved it?" vs "Where did this come from?") already carries
 * direction, and `money()` isn't built to print a leading minus sign. */
export interface TriageRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  payee: string | null;
  description: string | null;
  label: string;
  account_name: string;
  resolved_flow: string;
  user_flow: string | null;
  signature: string;
  signature_count: number;
  signature_amount: number;
  user_note: string | null;
  suggested_flow?: string | null;
}

interface Props {
  title: string;
  prompt: string;
  rows: TriageRow[];
  choices: TriageChoice[];
  // `note` is optional so the answer can carry free text without forcing
  // every caller to pass one — an existing caller with a two-arg handler
  // stays type-valid (spec §3: notes are optional, ride along with the
  // answer, and a note alone never clears a row).
  onAnswer: (id: string, flow: string, note?: string) => void;
  onBulk: (ids: string[], flow: string) => void;
}

/**
 * A "just answered" row, kept purely to decide whether to show ONE bulk-offer
 * line beneath it. Whether a row is answered in the persisted sense — and
 * whether it's still in `rows` at all — is the PARENT's concern: Task 8
 * removes answered rows from the array it passes in (optimistically, ahead
 * of the request resolving). This component never removes a row itself; it
 * only remembers enough, for the moment the row is still on screen, to offer
 * "apply to the others" once.
 */
interface JustAnswered {
  id: string;
  flow: string;
}

/** Display-only capitalization for a bulk-offer signature ("venmo" -> "Venmo",
 * "cash app" -> "Cash App", "atm" -> "ATM"). Purely presentational — the
 * signature itself stays lowercase everywhere else (matching, grouping), so
 * this lives here rather than in lib.ts. */
function displaySignature(signature: string): string {
  if (signature.toLowerCase() === "atm") return "ATM";
  return signature
    .split(" ")
    .map((word) => (word ? word[0].toUpperCase() + word.slice(1) : word))
    .join(" ");
}

export default function TriageQueue({ title, prompt, rows, choices, onAnswer, onBulk }: Props) {
  const [justAnswered, setJustAnswered] = useState<JustAnswered | null>(null);
  // Per-row note UI state, keyed by simplefin_id. Kept here rather than on
  // the row itself: a note is draft input that only becomes real when a
  // choice chip is tapped alongside it (spec §3) — it's never independently
  // saved, so it has no business living in the row data the parent owns.
  const [openNotes, setOpenNotes] = useState<Record<string, boolean>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});

  if (rows.length === 0) {
    return (
      <section className="triage-queue">
        <h3>{title}</h3>
        <p className="quiet empty">Nothing to sort out.</p>
      </section>
    );
  }

  const toggleNote = (id: string) => {
    setOpenNotes((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const setDraft = (id: string, value: string) => {
    setNoteDrafts((prev) => ({ ...prev, [id]: value }));
  };

  const handleAnswer = (row: TriageRow, flow: string) => {
    // Trimmed, and only passed at all when non-empty — an accidental clear
    // from this surface must be impossible (spec §3: omitting the key on
    // the write path leaves the stored note untouched; "" would clear it).
    const trimmed = noteDrafts[row.simplefin_id]?.trim();
    const note = trimmed ? trimmed : undefined;
    setJustAnswered({ id: row.simplefin_id, flow });
    onAnswer(row.simplefin_id, flow, note);
  };

  const handleBulk = (row: TriageRow, flow: string) => {
    const ids = rows
      .filter((r) => r.simplefin_id !== row.simplefin_id && r.signature === row.signature)
      .map((r) => r.simplefin_id);
    onBulk(ids, flow);
  };

  return (
    <section className="triage-queue">
      <h3>{title}</h3>
      <p className="triage-prompt">{prompt}</p>
      <ul className="triage-list">
        {rows.map((row) => {
          const answeredWith = justAnswered?.id === row.simplefin_id ? justAnswered : null;
          const { monthDay } = dayRowDate(row.posted);
          const noteOpen = !!openNotes[row.simplefin_id];
          // Suggestion only surfaces on an unanswered row, and only when a
          // chip in THIS queue's choices actually matches (spec §5) — the
          // two queues have disjoint flow sets, so a mismatch is defensive
          // rather than expected, and should render nothing rather than a
          // hint with no highlighted chip to match it.
          const hint = answeredWith ? "" : suggestionHint(row.suggested_flow);
          const showHint = hint !== "" && choices.some((c) => c.flow === row.suggested_flow);

          return (
            <li key={row.simplefin_id} className={`triage-row${answeredWith ? " settle" : ""}`}>
              <div className="triage-row-top">
                <span className="triage-detail">
                  <span className="triage-label">{row.label}</span>
                  {row.user_note && <span className="triage-note">{row.user_note}</span>}
                  <span className="triage-meta">
                    {row.account_name} · {monthDay}
                    <button
                      type="button"
                      className="triage-note-toggle"
                      onClick={() => toggleNote(row.simplefin_id)}
                    >
                      Add note
                    </button>
                  </span>
                  {showHint && <span className="triage-hint">{hint}</span>}
                </span>
                <span className="triage-amount num">{money(Math.abs(row.amount))}</span>
              </div>

              {noteOpen && (
                <input
                  type="text"
                  className="triage-note-input"
                  maxLength={500}
                  placeholder="What's this?"
                  value={noteDrafts[row.simplefin_id] ?? ""}
                  onChange={(e) => setDraft(row.simplefin_id, e.target.value)}
                />
              )}

              <div className="chips">
                {choices.map((c) => (
                  <button
                    key={c.flow}
                    className={showHint && c.flow === row.suggested_flow ? "chip-suggested" : undefined}
                    onClick={() => handleAnswer(row, c.flow)}
                  >
                    {c.label}
                  </button>
                ))}
              </div>

              {answeredWith && row.signature_count >= 2 && (
                <button className="quiet-btn" onClick={() => handleBulk(row, answeredWith.flow)}>
                  Apply to the other {row.signature_count} {displaySignature(row.signature)} charges · {money(row.signature_amount)}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
