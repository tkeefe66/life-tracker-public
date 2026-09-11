import { weekLabel } from "../lib";

interface Props {
  weekStart: string;
  isCurrent: boolean;
  max: string;
  onPrev: () => void;
  onNext: () => void;
  onPick: (iso: string) => void;
}

export default function WeekNav({ weekStart, isCurrent, max, onPrev, onNext, onPick }: Props) {
  return (
    <div className={`navhead${isCurrent ? "" : " past"}`}>
      <button className="nav-btn" aria-label="Previous week" onClick={onPrev}>‹</button>
      <div className="nav-label">
        <input
          className="nav-pick"
          type="date"
          value={weekStart}
          max={max}
          aria-label="Jump to a week"
          onClick={(e) => {
            // Desktop browsers only open the popover from the calendar icon;
            // showPicker() opens it from anywhere in the label.
            try { e.currentTarget.showPicker?.(); } catch { /* fall back to native */ }
          }}
          onChange={(e) => {
            const v = e.target.value;
            if (v && v <= max) onPick(v);
          }}
        />
        <h2>{isCurrent ? "This week" : "Week of"}</h2>
        <p className="sub">{weekLabel(weekStart)}</p>
      </div>
      <button className="nav-btn" aria-label="Next week" onClick={onNext} disabled={isCurrent}>›</button>
    </div>
  );
}
