import { useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { money, targetLabel, weekRangeLabel } from "../lib";
import TrendChart from "../components/TrendChart";
import WeekdayHeatmap from "../components/WeekdayHeatmap";

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { week_start: string; metrics: Record<string, Metric> }
interface DatesSummary {
  weekly: { week_start: string; count: number }[];
  count: number;
  total_spend: number;
  avg_spend: number;
  top_places: { place: string; count: number; spend: number }[];
}
interface InsightsData {
  weeks: Card[];
  streaks: Record<string, number>;
  weekday_counts: Record<string, number[]>;
  noticings: string[];
  dates: DatesSummary;
}
interface Reflection { week_start: string; text: string }

const ORDER = ["gym", "social", "delivery", "alcohol", "substances"];

function hasAnyData(ins: InsightsData): boolean {
  return ins.weeks.some((w) => Object.values(w.metrics).some((m) => m.count > 0));
}

export default function Insights() {
  // ── Behavior view data — moved from Scorecard.tsx unchanged ──
  const [card, setCard] = useState<Card | null>(null);
  const [insights, setInsights] = useState<InsightsData | null>(null);
  const [reflection, setReflection] = useState<Reflection | null>(null);
  const [selected, setSelected] = useState<Record<string, number | null>>({});

  useEffect(() => {
    apiGet<Card>("/scorecard").then(setCard).catch(() => setCard(null));
  }, []);

  useEffect(() => {
    apiGet<InsightsData>("/insights?weeks=12").then(setInsights).catch(() => setInsights(null));
  }, []);

  useEffect(() => {
    apiSend<Reflection | null>("POST", "/reflection").then(setReflection).catch(() => setReflection(null));
  }, []);

  return (
    <div>
      {card && insights && hasAnyData(insights) && (
        <>
          <h2 className="section-label">Trends · last 12 weeks</h2>
          <div className="trends">
            {ORDER.map((key) => {
              const m = card.metrics[key];
              const points = insights.weeks.map((w) => ({
                count: w.metrics[key].count, hit: w.metrics[key].hit, weekStart: w.week_start,
              }));
              const allZero = points.every((p) => p.count === 0);
              const selectedIndex = selected[key] ?? null;
              const selectedPoint = selectedIndex !== null ? points[selectedIndex] : null;
              return (
                <div className="trend-row" key={key}>
                  <div className="trend-row-head">
                    <span className="trend-name">{m.label}</span>
                    <span className="trend-current">
                      <span className="num">{m.count}</span> of {targetLabel(m.direction, m.target)}
                    </span>
                  </div>
                  {allZero ? (
                    <p className="trend-empty">No data yet</p>
                  ) : (
                    <>
                      <TrendChart
                        points={points}
                        target={m.target}
                        direction={m.direction}
                        onSelect={(i) =>
                          setSelected((prev) => ({ ...prev, [key]: prev[key] === i ? null : i }))
                        }
                      />
                      {selectedPoint && (
                        <p className="trend-caption">
                          {weekRangeLabel(selectedPoint.weekStart)} · {selectedPoint.count}
                        </p>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>

          <h2 className="section-label">Patterns · by weekday, last 8 weeks</h2>
          <WeekdayHeatmap
            rows={ORDER.map((key) => ({
              label: card.metrics[key].label,
              counts: insights.weekday_counts[key] ?? [0, 0, 0, 0, 0, 0, 0],
              caution: card.metrics[key].direction === "ceiling",
            }))}
          />

          <details className="numbers">
            <summary>Show the numbers</summary>
            <div className="numbers-scroll">
              <table className="numbers-table">
                <thead>
                  <tr>
                    <th>Week</th>
                    {ORDER.map((key) => <th key={key}>{card.metrics[key].label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {[...insights.weeks].reverse().map((w) => (
                    <tr key={w.week_start}>
                      <td>{weekRangeLabel(w.week_start)}</td>
                      {ORDER.map((key) => (
                        <td key={key} className="num">{w.metrics[key].count}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>

          {insights.noticings.length > 0 && (
            <>
              <h2 className="section-label">Noticings</h2>
              {insights.noticings.map((n) => (
                <p className="quiet" key={n}><span>{n}</span></p>
              ))}
            </>
          )}
        </>
      )}

      {/* Unscored dates series (2026-07-30 date-tracking spec §6): hidden
          entirely at zero — a secondary surface never nags. Not gated on
          hasAnyData: dates aren't a METRICS entry, so a week of only dates
          should still show this panel. */}
      {insights && insights.dates.count > 0 && (
        <>
          <h2 className="section-label">Dates · last 8 weeks</h2>
          <div className="dates-panel">
            <div className="dates-stats">
              <div><strong>{insights.dates.count}</strong> dates</div>
              <div><strong>{money(insights.dates.total_spend)}</strong> total</div>
              <div><strong>{money(insights.dates.avg_spend)}</strong> avg</div>
            </div>
            <svg
              className="dates-bars"
              viewBox="0 0 360 48"
              role="img"
              aria-label={`Dates per week, last ${insights.dates.weekly.length} weeks`}
              preserveAspectRatio="xMidYMid meet"
            >
              {(() => {
                const weekly = insights.dates.weekly;
                const max = Math.max(1, ...weekly.map((w) => w.count));
                const bw = 356 / weekly.length;
                return weekly.map((w, i) => {
                  const h = (w.count / max) * 40;
                  return (
                    <rect
                      key={w.week_start}
                      x={2 + i * bw + 1}
                      y={44 - h}
                      width={Math.max(bw - 2, 1)}
                      height={Math.max(h, w.count > 0 ? 2 : 0)}
                      rx="1.5"
                      className="dates-bar"
                    />
                  );
                });
              })()}
            </svg>
            {insights.dates.top_places.length > 0 && (
              <ul className="dates-places">
                {insights.dates.top_places.map((p) => (
                  <li key={p.place}>
                    <span>{p.place}</span>
                    <span className="muted">
                      {p.count}×{p.spend > 0 ? ` · ${money(p.spend)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
      {insights && !hasAnyData(insights) && (
        <p className="footnote">Not enough history yet — insights appear after a few weeks.</p>
      )}

      {reflection && (
        <>
          <h2 className="section-label">Last week</h2>
          <p className="reflection">{reflection.text}</p>
        </>
      )}
    </div>
  );
}
