import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { addDays, mondayOf, type Day, type SpendRow } from "../lib";
import WeekNav from "../components/WeekNav";
import WeekDays from "../components/WeekDays";
import SpendSubtotals from "../components/SpendSubtotals";

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card {
  week_start: string; week_end: string; metrics: Record<string, Metric>;
  delivery_spend: number; social_spend: number; spend_by_service: SpendRow[];
}
interface WeekDaysData { week_start: string; week_end: string; week_total: number; days: Day[] }

const ORDER = ["gym", "social", "delivery", "alcohol", "substances"];
// Matches Today's week-strip abbreviations — same metrics, same short labels.
const SHORT_LABELS: Record<string, string> = {
  gym: "Gym", social: "Social", delivery: "Delivery", alcohol: "Alcohol", substances: "Subst.",
};

interface Props {
  /** Opens a day on Today, carrying its date — the date button and the
   * "Open <Weekday> →" panel link both call this. */
  onOpenDay: (iso: string) => void;
}

export default function Scorecard({ onOpenDay }: Props) {
  const [card, setCard] = useState<Card | null>(null);
  const [weekDays, setWeekDays] = useState<WeekDaysData | null>(null);
  const [currentWeekStart, setCurrentWeekStart] = useState<string | null>(null);
  const [currentWeekEnd, setCurrentWeekEnd] = useState<string | null>(null);
  const [weekStart, setWeekStart] = useState<string | null>(null); // null = current
  const [error, setError] = useState("");

  useEffect(() => {
    apiGet<Card>(`/scorecard${weekStart ? `?week_start=${weekStart}` : ""}`)
      .then((c) => {
        setCard(c);
        if (!weekStart) {
          setCurrentWeekStart(c.week_start);
          setCurrentWeekEnd(c.week_end);
        }
      })
      .catch((e) => setError(e.message));
  }, [weekStart]);

  // A secondary surface — a failed fetch hides the day card quietly rather
  // than blanking the whole screen.
  useEffect(() => {
    apiGet<WeekDaysData>(`/week-days${weekStart ? `?week_start=${weekStart}` : ""}`)
      .then(setWeekDays)
      .catch(() => setWeekDays(null));
  }, [weekStart]);

  if (error) return <p className="error">{error}</p>;
  if (!card) return <p className="center">Loading…</p>;

  return (
    <div>
      <WeekNav
        weekStart={card.week_start}
        isCurrent={card.week_start === (currentWeekStart ?? card.week_start)}
        onPrev={() => setWeekStart(addDays(card.week_start, -7))}
        onNext={() => {
          const next = addDays(card.week_start, 7);
          setWeekStart(next === currentWeekStart ? null : next);
        }}
        max={currentWeekEnd ?? card.week_end}
        onPick={(iso) => {
          const monday = mondayOf(iso);
          setWeekStart(monday === currentWeekStart ? null : monday);
        }}
      />

      {/* Compact five-tile ledger: count + short label only. A metric's hit/miss
          detail (target, streak, trend) now lives in Insights — this strip just
          answers "how many" at a glance. */}
      <div className="ledger-strip">
        {ORDER.map((key) => {
          const m = card.metrics[key];
          return (
            <div className="ledger-tile" key={key}>
              <span className={`ledger-count num${m.hit ? "" : " over"}`}>{m.count}</span>
              <span className="ledger-label">{SHORT_LABELS[key]}</span>
            </div>
          );
        })}
      </div>

      {weekDays && (
        <WeekDays days={weekDays.days} weekTotal={weekDays.week_total} onOpenDay={onOpenDay} />
      )}

      <SpendSubtotals rows={card.spend_by_service} title="Spent this week" />
    </div>
  );
}
