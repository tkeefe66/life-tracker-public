import CategoryIcon from "./CategoryIcon";
import type { DayLogCategory } from "../lib";

interface Props {
  categories: DayLogCategory[];
  active: DayLogCategory | null;
  onSelect: (category: DayLogCategory | null) => void;
}

/**
 * Tap-to-filter category strip (spec: 2026-07-30-day-log-redesign-design,
 * §2): shows only the categories present that day (typically 2-4, never
 * the full closed set of six). Tapping the active chip again clears the
 * filter — there is no separate "All" control, so re-tapping is the only
 * way back to the unfiltered view. Renders nothing on a day with no rows.
 */
export default function FilterStrip({ categories, active, onSelect }: Props) {
  if (categories.length === 0) return null;

  return (
    <div className="filter-strip" role="group" aria-label="Filter day log by category">
      {categories.map((c) => (
        <button
          key={c}
          type="button"
          className={`filter-chip${active === c ? " on" : ""}`}
          aria-pressed={active === c}
          aria-label={`Filter to ${c}`}
          onClick={() => onSelect(active === c ? null : c)}
        >
          <CategoryIcon category={c} />
        </button>
      ))}
    </div>
  );
}
