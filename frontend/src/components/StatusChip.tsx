import type { MouseEvent } from "react";

type Props =
  | { kind: "social" }
  | { kind: "date" }
  | { kind: "uncertain"; onYes: () => void; onNo: () => void }
  | { kind: "removed"; onUndo: () => void };

/**
 * The tiny, fixed vocabulary of scoring-status chips (spec:
 * 2026-07-30-day-log-redesign-design, §4/§7): `social`, inline
 * `social? Yes/No`, `Removed · Undo`, and — added as the sanctioned
 * deliberate design event by the 2026-07-30 date-tracking spec — `date`
 * (same visual family as `social`; the label text differentiates). This
 * set does not grow casually — a new scoring signal earns its own chip
 * only as a deliberate design event, the same weight as adding a category
 * or a chart color.
 *
 * Always rendered *inside* the row it describes (§7 — the bug this fixes:
 * the ambiguity chip used to float on its own line with no visual
 * attachment to its event). `onClick` stops propagation so tapping Yes/No/
 * Undo never also triggers the row's own tap-to-edit action.
 */
export default function StatusChip(props: Props) {
  const stop = (e: MouseEvent) => e.stopPropagation();

  if (props.kind === "social") {
    return <span className="chip chip-accent">social</span>;
  }

  if (props.kind === "date") {
    return <span className="chip chip-accent">date</span>;
  }

  if (props.kind === "uncertain") {
    return (
      <span className="chip-question-group" onClick={stop}>
        <span className="chip chip-question">social?</span>
        <button type="button" onClick={props.onYes}>Yes</button>
        <button type="button" onClick={props.onNo}>No</button>
      </span>
    );
  }

  return (
    <span className="chip-removed-group" onClick={stop}>
      Removed · <button type="button" className="undo-btn" onClick={props.onUndo}>Undo</button>
    </span>
  );
}
