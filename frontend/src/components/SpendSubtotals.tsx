import { categoryForKind, money, serviceLabel, type SpendRow } from "../lib";
import CategoryIcon from "./CategoryIcon";

interface Props {
  rows: SpendRow[];
  title?: string;
}

/** One line per service (icon + label left, amount right) plus a Total line.
 * The category glyph is the same primitive the Day log uses (spec:
 * 2026-07-30-day-log-redesign-design, §8) — consistency by shared
 * vocabulary, not by restructuring this screen. Meta stays amount-only:
 * subtotals have no times. Renders nothing when there is no spend at all —
 * a quiet screen stays quiet. */
export default function SpendSubtotals({ rows, title }: Props) {
  if (rows.length === 0 || rows.every((r) => r.amount === 0)) return null;
  const total = rows.reduce((sum, r) => sum + r.amount, 0);

  return (
    <>
      {title && <h2 className="section-label">{title}</h2>}
      <div className="spend">
        {rows.map((r) => (
          <div className="spend-row" key={`${r.kind}:${r.service}`}>
            <span className="spend-service">
              <CategoryIcon category={categoryForKind(r.kind)} />
              {serviceLabel(r.kind, r.service)}
            </span>
            <span className="spend-amount num">{money(r.amount)}</span>
          </div>
        ))}
        <div className="spend-row spend-total">
          <span>Total</span>
          <span className="num">{money(total)}</span>
        </div>
      </div>
    </>
  );
}
