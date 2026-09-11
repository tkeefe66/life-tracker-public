import { dayLabel, relativeDayLabel } from "../lib";

interface Props {
  date: string;
  todayIso: string;
  onPrev: () => void;
  onNext: () => void;
  onPick: (iso: string) => void;
}

export default function DayNav({ date, todayIso, onPrev, onNext, onPick }: Props) {
  const isToday = date === todayIso;
  return (
    <div className={`navhead${isToday ? "" : " past"}`}>
      <button className="nav-btn" aria-label="Previous day" onClick={onPrev}>‹</button>
      <div className="nav-label">
        <input
          className="nav-pick"
          type="date"
          value={date}
          max={todayIso}
          aria-label="Pick a date"
          onClick={(e) => {
            // Desktop browsers only open the popover from the calendar icon;
            // showPicker() opens it from anywhere in the label.
            try { e.currentTarget.showPicker?.(); } catch { /* fall back to native */ }
          }}
          onChange={(e) => {
            const v = e.target.value;
            if (v && v <= todayIso) onPick(v);
          }}
        />
        <h2>{relativeDayLabel(date, todayIso)}</h2>
        <p className="sub">{dayLabel(date)}</p>
      </div>
      <button className="nav-btn" aria-label="Next day" onClick={onNext} disabled={isToday}>›</button>
    </div>
  );
}
