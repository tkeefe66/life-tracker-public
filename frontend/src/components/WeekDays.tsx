import { useState } from "react";
import { dayChips, dayLabel, dayRowDate, money, type Day } from "../lib";

interface Props {
  days: Day[];
  weekTotal: number;
  onOpenDay: (iso: string) => void;
}

/**
 * One row per day, Monday-first, always all seven. Two separate real
 * controls per row: the date button opens that day on Today, the rest of
 * the row (chips, cost, chevron) expands an itemised panel. Only one day
 * is expanded at a time.
 */
export default function WeekDays({ days, weekTotal, onOpenDay }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="weekdays">
      {days.map((day) => {
        const isOpen = expanded === day.date;
        const chips = dayChips(day);
        // A day counts as "nothing logged" only when truly nothing happened —
        // a work-only ride still has an item (shown in the panel) even though
        // it produces no chip and contributes nothing to the total.
        const isEmpty = day.items.length === 0 && !day.gym && day.alcohol_level == null && !day.substances;
        const { weekday, monthDay } = dayRowDate(day.date);
        const label = dayLabel(day.date);
        const panelId = `day-panel-${day.date}`;

        return (
          <div className="day-row" key={day.date}>
            <div className="day-row-head">
              <button className="day-date" aria-label={`Open ${label}`} onClick={() => onOpenDay(day.date)}>
                <span className="day-weekday">{weekday}</span>
                <span className="day-monthday">{monthDay}</span>
              </button>

              <button
                className="day-expand"
                aria-expanded={isOpen}
                aria-controls={panelId}
                aria-label={`${isOpen ? "Collapse" : "Expand"} details for ${label}`}
                onClick={() => setExpanded(isOpen ? null : day.date)}
              >
                {isEmpty ? (
                  <span className="day-empty muted">Nothing logged</span>
                ) : chips.length === 0 ? (
                  <span className="day-chips muted">Work travel</span>
                ) : (
                  <span className="day-chips">
                    {chips.map((c) => (
                      <span className={`chip chip-${c.tone}`} key={c.label}>{c.label}</span>
                    ))}
                  </span>
                )}
                <span className="day-cost num">{day.total > 0 ? money(day.total) : "—"}</span>
                <span className={`day-chevron${isOpen ? " open" : ""}`} aria-hidden="true">⌄</span>
              </button>
            </div>

            <div className="day-panel" id={panelId} hidden={!isOpen}>
              {day.items.length === 0 ? (
                <p className="day-panel-empty muted">Nothing logged</p>
              ) : (
                day.items.map((item, i) => (
                  <div className="day-panel-item" key={i}>
                    <span className="day-panel-label">
                      {item.label}
                      {item.is_work && <span className="day-panel-tag muted"> · work</span>}
                    </span>
                    <span className="day-panel-amount num">{money(item.amount)}</span>
                  </div>
                ))
              )}
              <button className="day-panel-link" onClick={() => onOpenDay(day.date)}>
                Open {label} →
              </button>
            </div>
          </div>
        );
      })}

      <div className="day-row day-total-row">
        <span>Total</span>
        <span className="num">{money(weekTotal)}</span>
      </div>
    </div>
  );
}
