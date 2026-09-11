import { categoryGlyph, type DayLogCategory } from "../lib";

interface Props {
  category: DayLogCategory;
}

/**
 * The six-category glyph (spec: 2026-07-30-day-log-redesign-design, §3-4) —
 * emoji only, no color fields, ever. Shared between Day log rows and the
 * Week screen's SpendSubtotals rows (§8) so the category vocabulary reads
 * the same everywhere it appears.
 */
export default function CategoryIcon({ category }: Props) {
  return (
    <span className="category-icon" aria-hidden="true">
      {categoryGlyph(category)}
    </span>
  );
}
