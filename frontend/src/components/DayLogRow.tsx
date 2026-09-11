import type { KeyboardEvent, ReactNode } from "react";
import CategoryIcon from "./CategoryIcon";
import type { DayLogCategory } from "../lib";

interface Props {
  category: DayLogCategory;
  name: string;
  meta?: string;
  chip?: ReactNode;
  interactive?: boolean;
  onClick?: () => void;
  dimmed?: boolean;
  removed?: boolean;
}

/**
 * One row of the Day log (spec: 2026-07-30-day-log-redesign-design, §6):
 * `[icon] name … [chip] time · $amount ›`. The chip sits inside the meta so
 * amounts right-align down the whole list; time sits left of the amount —
 * `meta` is pre-formatted by the caller (see `dayLogRowMeta` in lib.ts) so
 * this component stays presentation-only.
 *
 * Interactive rows render as a `div[role="button"]` rather than a native
 * `<button>`: the uncertain and removed status chips carry their own nested
 * buttons (Yes/No, Undo), and a `<button>` inside a `<button>` is invalid
 * HTML — browsers silently break the DOM rather than rejecting it outright.
 *
 * Filtered-out rows dim (`dimmed`) rather than unmount (spec §2) — the
 * FilterStrip's whole point is that the day's shape stays visible even
 * while narrowed to one category.
 */
export default function DayLogRow({
  category, name, meta, chip, interactive = false, onClick, dimmed = false, removed = false,
}: Props) {
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!interactive) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick?.();
    }
  };

  return (
    <div
      className={`day-log-row${dimmed ? " dim" : ""}${removed ? " removed" : ""}`}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={interactive ? onClick : undefined}
      onKeyDown={handleKeyDown}
    >
      <CategoryIcon category={category} />
      <span className="day-log-name">{name}</span>
      {chip}
      {meta && <span className="day-log-meta">{meta}</span>}
      {interactive && <span className="day-log-chev" aria-hidden="true">›</span>}
    </div>
  );
}
