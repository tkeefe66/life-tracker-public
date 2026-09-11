import { weekCaption, weekRangeLabel, type WeekSpendPoint } from "../lib";

interface Props {
  weeks: WeekSpendPoint[];
  onSelect?: (index: number) => void;
}

// Same geometry as SpendChart — single series, so no stacking and no legend.
const VB_W = 360, VB_H = 96;
const PLOT_TOP = 8, BASELINE = 64;
const PLOT_H = BASELINE - PLOT_TOP;
const PLOT_LEFT = 2, PLOT_RIGHT = VB_W - 2;
const AXIS_Y = 80;

export default function BankSpendChart({ weeks, onSelect }: Props) {
  if (weeks.length === 0) return null;

  const max = Math.max(...weeks.map((w) => w.spending), 1) * 1.15; // headroom, matches SpendChart
  const bw = (PLOT_RIGHT - PLOT_LEFT) / weeks.length;
  const lastIndex = weeks.length - 1;

  return (
    <svg
      className="spend-chart"
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      role="img"
      aria-label={`Weekly bank spending for the last ${weeks.length} weeks`}
      preserveAspectRatio="xMidYMid meet"
    >
      <line className="spend-baseline" x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={BASELINE} y2={BASELINE} />

      {weeks.map((w, i) => {
        const colX = PLOT_LEFT + i * bw;
        const barX = colX + 1;
        const barWidth = Math.max(bw - 2, 1);
        // Refunds can net a week negative (spec §2) — the backend reports the
        // true net so the tap caption tells the truth, but bar geometry has
        // no way to draw "negative", so it's floored at zero here. The
        // `<title>`/`aria-label` and `onSelect` below stay wired to `w`
        // itself, never this floored value, so the caption keeps the sign.
        const barSpending = Math.max(0, w.spending);
        const hasBar = barSpending > 0;
        const h = Math.max((barSpending / max) * PLOT_H, 1);
        const barY = BASELINE - h;
        const title = weekCaption(w);

        return (
          <g key={w.week_start}>
            {hasBar && (
              <rect
                className={`spend-seg spend-seg-bank${w.partial ? " spend-seg-current" : ""}`}
                x={barX} y={barY} width={barWidth} height={h}
              />
            )}
            {hasBar && w.partial && (
              <line
                className="spend-seg-current-edge"
                x1={barX} x2={barX + barWidth}
                y1={barY} y2={barY}
              />
            )}
            <rect
              className="spend-hit"
              x={colX} y={0} width={bw} height={VB_H}
              tabIndex={onSelect ? 0 : -1}
              role={onSelect ? "button" : undefined}
              aria-label={title}
              onClick={() => onSelect?.(i)}
              onKeyDown={(e) => {
                if (!onSelect) return;
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(i);
                }
              }}
            >
              <title>{title}</title>
            </rect>
          </g>
        );
      })}

      <text className="spend-axis" x={PLOT_LEFT} y={AXIS_Y} textAnchor="start">
        {weekRangeLabel(weeks[0].week_start)}
      </text>
      <text className="spend-axis" x={PLOT_RIGHT} y={AXIS_Y} textAnchor="end">
        {weekRangeLabel(weeks[lastIndex].week_start)}
      </text>
    </svg>
  );
}
