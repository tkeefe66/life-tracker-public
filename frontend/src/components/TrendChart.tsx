import { niceMax, targetLabel, weekRangeLabel } from "../lib";

interface Point { count: number; hit: boolean; weekStart: string }
interface Props {
  points: Point[];
  target: number;
  direction: string;
  unit?: string;
  onSelect?: (index: number) => void;
}

const VB_W = 360, VB_H = 96;
const PLOT_TOP = 8, BASELINE = 64;
const PLOT_H = BASELINE - PLOT_TOP;
const PLOT_LEFT = 2, PLOT_RIGHT = VB_W - 2;
const AXIS_Y = 80;

export default function TrendChart({ points, target, direction, unit, onSelect }: Props) {
  if (points.length === 0) return null;

  const max = niceMax(points.map((p) => p.count), target);
  const bw = (PLOT_RIGHT - PLOT_LEFT) / points.length;
  const y = (v: number) => BASELINE - (v / max) * PLOT_H;
  const targetY = y(target);
  const lastIndex = points.length - 1;
  const tLabel = targetLabel(direction, target);
  const unitSuffix = unit ? ` ${unit}` : "";

  return (
    <svg
      className="trend"
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      role="img"
      aria-label={`Weekly counts for the last ${points.length} weeks, target ${tLabel}`}
      preserveAspectRatio="xMidYMid meet"
    >
      {points.map((p, i) => {
        const colX = PLOT_LEFT + i * bw;
        const barX = colX + 1;
        const barWidth = Math.max(bw - 2, 1);
        const isZero = p.count === 0;
        const rawY = y(p.count);
        const barY = isZero ? BASELINE - 2 : Math.min(rawY, BASELINE - 1);
        const barHeight = isZero ? 2 : Math.max(BASELINE - barY, 1);
        const fillClass = isZero ? "zero" : p.hit ? "hit" : "over";
        const title = `${weekRangeLabel(p.weekStart)} · ${p.count}${unitSuffix}`;
        const isLast = i === lastIndex;
        const roomAbove = barY - PLOT_TOP >= 10;

        return (
          <g key={p.weekStart}>
            <rect
              className={`trend-bar ${fillClass}`}
              x={barX} y={barY} width={barWidth} height={barHeight} rx="2"
            />
            {isLast && (
              <text
                className="trend-value"
                x={barX + barWidth / 2}
                y={roomAbove ? barY - 4 : barY + 10}
                textAnchor="middle"
              >
                {p.count}
              </text>
            )}
            <rect
              className="trend-hit"
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

      <line className="trend-target" x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={targetY} y2={targetY} />
      <text className="trend-target-label" x={PLOT_RIGHT} y={targetY - 3} textAnchor="end">
        {tLabel}
      </text>

      <text className="trend-axis" x={PLOT_LEFT} y={AXIS_Y} textAnchor="start">
        {weekRangeLabel(points[0].weekStart)}
      </text>
      <text className="trend-axis" x={PLOT_RIGHT} y={AXIS_Y} textAnchor="end">
        {weekRangeLabel(points[lastIndex].weekStart)}
      </text>
    </svg>
  );
}
