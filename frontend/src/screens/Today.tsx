import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { apiGet, apiSend } from "../api";
import {
  addDays, buildSocialPatch, buildUncertainResolvePatch, categoryForKind, dayLogRowMeta,
  isDimmed, mergeRemovedSocialEvents, mondayOf, nudgeLabel, nudgeOptions, orderDayLog,
  presentCategories, subtotalsFromDay, targetLabel, weekRangeLabel, type DayLogCategory,
} from "../lib";
import DayNav from "../components/DayNav";
import DayLogRow from "../components/DayLogRow";
import FilterStrip from "../components/FilterStrip";
import StatusChip from "../components/StatusChip";
import SpendSubtotals from "../components/SpendSubtotals";

interface SocialEvent {
  gcal_event_id: string;
  title: string;
  start_at: string;
  end_at: string;
  source: string;
  amount: number | null;
  is_social: boolean;
  // True when the classifier hasn't decided confidently AND the user hasn't
  // answered — the row still shows (following the AI's lean for counting
  // purposes) but also carries the "social? Yes/No" ambiguity chip. See
  // docs/superpowers/specs/2026-07-30-social-classification-granularity-design.md.
  uncertain: boolean;
  // Resolved date flag + place (2026-07-30 date-tracking spec). A date shows
  // the "date" chip instead of "social" and is excluded from social counts.
  is_date: boolean;
  location: string | null;
}

interface Ride {
  id: number;
  service: string;
  ride_at: string;
  // Resolved TRUE ride time (parsed trip time when known, else ride_at) —
  // display this, not ride_at, which is only the email-arrival time.
  ride_time: string;
  subject: string;
  amount: number | null;
  ai_is_work: boolean | null;
  user_is_work: boolean | null;
  is_work: boolean;
  is_cancellation: boolean | null;
  // Resolved day (user nudge wins) and the cutoff-only automatic day —
  // the pair nudgeOptions needs to offer valid move targets.
  day: string;
  auto_day: string;
}

interface TodayData {
  date: string;
  gym: boolean;
  alcohol_level: number | null;
  substances: boolean;
  deliveries: {
    id: number; service: string; subject: string; ordered_at: string;
    amount: number | null; day: string; auto_day: string;
  }[];
  social_events: SocialEvent[];
  rides: Ride[];
}

interface Metric { label: string; count: number; target: number; direction: string; hit: boolean }
interface Card { metrics: Record<string, Metric> }

const LEVEL_HINTS = ["a drink or two", "a solid night", "a heavy one"];
const STRIP_ORDER = ["gym", "social", "delivery", "alcohol", "substances"];
const STRIP_LABELS: Record<string, string> = {
  gym: "Gym", social: "Social", delivery: "Delivery", alcohol: "Alcohol", substances: "Subst.",
};

interface Props {
  /** A date carried over from Week's "open this day" tap. Consumed once —
   * selects that day, then fires onConsumed() so the pin doesn't stick around
   * for the next time this screen mounts. */
  initialDate?: string | null;
  onConsumed?: () => void;
}

export default function Today({ initialDate, onConsumed }: Props = {}) {
  const [data, setData] = useState<TodayData | null>(null);
  const [week, setWeek] = useState<Card | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // null = today
  const [todayIso, setTodayIso] = useState<string | null>(null);
  const [error, setError] = useState("");

  // Wait for todayIso so the "selected === null means today" equivalence still
  // holds when initialDate happens to be today — otherwise this would pin a
  // date that should behave like "no selection".
  useEffect(() => {
    if (initialDate == null || todayIso == null) return;
    setSelected(initialDate === todayIso ? null : initialDate);
    onConsumed?.();
  }, [initialDate, todayIso]);

  const [addingSocial, setAddingSocial] = useState(false);
  const [socialName, setSocialName] = useState("");
  const [socialAmount, setSocialAmount] = useState("");
  const [socialLocation, setSocialLocation] = useState("");
  const [socialIsDate, setSocialIsDate] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editIsSocial, setEditIsSocial] = useState(true);
  const [editAmount, setEditAmount] = useState("");
  const [editIsDate, setEditIsDate] = useState(false);
  const [editLocation, setEditLocation] = useState("");
  const [editLoaded, setEditLoaded] = useState<{
    title: string; isSocial: boolean; amount: number | null;
    isDate: boolean; location: string | null;
  }>({ title: "", isSocial: true, amount: null, isDate: false, location: null });

  // Detected social events the user just marked "Didn't happen" this
  // session, keyed by gcal_event_id. The day query filters resolved-social
  // events, so a refresh() no longer returns these — this map is the only
  // reason the row keeps rendering (with its own Undo). Deliberately not
  // persisted anywhere; it resets whenever the visible day changes below.
  const [removed, setRemoved] = useState<Record<string, SocialEvent>>({});

  // The FilterStrip's selection is per-day, same reasoning as `removed`
  // above — a category active on one day has no meaning once the user
  // navigates to a different one.
  const [activeCategory, setActiveCategory] = useState<DayLogCategory | null>(null);

  // One action strip open at a time, keyed "delivery:3" / "ride:7" — same
  // per-day reset rule as the filter strip.
  const [openStrip, setOpenStrip] = useState<string | null>(null);

  useEffect(() => {
    setRemoved({});
    setActiveCategory(null);
    setOpenStrip(null);
  }, [data?.date]);

  const refresh = useCallback(() => {
    apiGet<TodayData>(`/today${selected ? `?date=${selected}` : ""}`)
      .then((d) => {
        setData(d);
        if (!selected) setTodayIso(d.date);
      })
      .catch((e) => setError(e.message));
    apiGet<Card>(`/scorecard${selected ? `?week_start=${selected}` : ""}`)
      .then(setWeek)
      .catch(() => setWeek(null));
  }, [selected]);
  useEffect(refresh, [refresh]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="center">Loading…</p>;

  const toggleGym = async () => {
    try {
      if (data.gym) await apiSend("DELETE", `/checkins/gym?date=${data.date}`);
      else await apiSend("POST", "/checkins", { type: "gym", date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const logAlcohol = async (level: number) => {
    try {
      await apiSend("POST", "/checkins", { type: "alcohol", level, date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const undoAlcohol = async () => {
    try {
      await apiSend("DELETE", `/checkins/alcohol?date=${data.date}`);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const toggleSubstances = async () => {
    try {
      if (data.substances) await apiSend("DELETE", `/checkins/substances?date=${data.date}`);
      else await apiSend("POST", "/checkins", { type: "substances", date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const openAddSocial = () => {
    setEditingId(null);
    setSocialName("");
    setSocialAmount("");
    setSocialLocation("");
    setSocialIsDate(false);
    setAddingSocial(true);
  };

  const cancelAddSocial = () => setAddingSocial(false);

  const submitAddSocial = async () => {
    const name = socialName.trim();
    if (!name) return;
    try {
      const amount = socialAmount.trim() === "" ? undefined : Number(socialAmount);
      await apiSend("POST", "/social", {
        name, date: data.date, amount,
        location: socialLocation.trim() || undefined,
        is_date: socialIsDate || undefined,
      });
      setAddingSocial(false);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const openEditSocial = (e: SocialEvent) => {
    setAddingSocial(false);
    setEditingId(e.gcal_event_id);
    setEditTitle(e.title);
    setEditIsSocial(e.is_social);
    setEditAmount(e.amount !== null ? String(e.amount) : "");
    setEditIsDate(e.is_date);
    setEditLocation(e.location ?? "");
    setEditLoaded({
      title: e.title, isSocial: e.is_social, amount: e.amount,
      isDate: e.is_date, location: e.location,
    });
  };

  const cancelEditSocial = () => setEditingId(null);

  const saveEditSocial = async () => {
    if (!editingId) return;
    const title = editTitle.trim();
    if (!title) return;
    try {
      // Only fields the user actually changed — an untouched checkbox or title
      // must never manufacture an override the user never made.
      const patch = buildSocialPatch({
        loadedTitle: editLoaded.title,
        loadedIsSocial: editLoaded.isSocial,
        loadedAmount: editLoaded.amount,
        loadedIsDate: editLoaded.isDate,
        loadedLocation: editLoaded.location,
        title: editTitle,
        isSocial: editIsSocial,
        amountText: editAmount,
        isDate: editIsDate,
        locationText: editLocation,
      });
      if (Object.keys(patch).length > 0) {
        await apiSend("PATCH", `/social/${editingId}`, patch);
      }
      setEditingId(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const deleteEditSocial = async () => {
    if (!editingId) return;
    try {
      await apiSend("DELETE", `/social/${editingId}`);
      setEditingId(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const didntHappenSocial = async (e: SocialEvent) => {
    try {
      // `removed` — "this occurrence didn't happen" — not `is_social`,
      // which means "this event type isn't social". Conflating the two
      // used to poison the classifier's few-shot examples with one-off
      // cancellations (see the granularity spec).
      await apiSend("PATCH", `/social/${e.gcal_event_id}`, { removed: true });
      setRemoved((prev) => ({ ...prev, [e.gcal_event_id]: e }));
      setEditingId(null);
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const undoDidntHappen = async (e: SocialEvent) => {
    try {
      await apiSend("PATCH", `/social/${e.gcal_event_id}`, { removed: false });
      setRemoved((prev) => {
        const next = { ...prev };
        delete next[e.gcal_event_id];
        return next;
      });
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const resolveUncertain = async (e: SocialEvent, isSocial: boolean) => {
    try {
      await apiSend("PATCH", `/social/${e.gcal_event_id}`, buildUncertainResolvePatch(isSocial));
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const toggleRideWork = async (r: Ride) => {
    try {
      // Confirming also teaches future classification — the API folds
      // confirmed overrides back into the AI's examples on the next scan.
      await apiSend("PATCH", `/rides/${r.id}`, { is_work: !r.is_work });
      setOpenStrip(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const moveDelivery = async (id: number, day: string) => {
    try {
      await apiSend("PATCH", `/deliveries/${id}`, { day });
      setOpenStrip(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const moveRide = async (id: number, day: string) => {
    try {
      await apiSend("PATCH", `/rides/${id}`, { day });
      setOpenStrip(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Merge in any just-removed rows the day query no longer returns, so the
  // "Nothing this day" empty state and the Undo row agree with each other.
  const socialRows = mergeRemovedSocialEvents(data.social_events, removed);

  // The Day log (spec: 2026-07-30-day-log-redesign-design) — one
  // chronological feed of deliveries, rides, social events, and the day's
  // logged check-ins. Each entry carries `timeIso` for `orderDayLog` (§1/§6)
  // and `category` for the FilterStrip (§2/§3); `node` is the rendered row
  // (plus, for social events, its inline edit form).
  interface LogEntry { key: string; timeIso: string | null; category: DayLogCategory; node: ReactNode }

  // Nudge buttons must never offer a future day; todayIso is set on first
  // load (viewing a past day means it's already known), data.date is the
  // safe fallback when the viewed day IS today.
  const todayForNudge = todayIso ?? data.date;

  const nudgeButtons = (autoDay: string, onMove: (day: string) => void) =>
    nudgeOptions(autoDay, data.date, todayForNudge).map((day) => (
      <button key={day} type="button" onClick={() => onMove(day)}>
        {day < data.date ? "‹ " : ""}Move to {nudgeLabel(day)}{day > data.date ? " ›" : ""}
      </button>
    ));

  const deliveryEntries: LogEntry[] = data.deliveries.map((d) => {
    const category = categoryForKind("delivery");
    const key = `delivery:${d.id}`;
    return {
      key,
      timeIso: d.ordered_at,
      category,
      node: (
        <div className="day-log-entry" key={key}>
          <DayLogRow
            category={category}
            name={`${d.service} order`}
            meta={dayLogRowMeta(d.ordered_at, d.amount)}
            interactive
            onClick={() => setOpenStrip(openStrip === key ? null : key)}
            dimmed={isDimmed(category, activeCategory)}
          />
          {openStrip === key && (
            <div className="day-log-actions">
              {nudgeButtons(d.auto_day, (day) => moveDelivery(d.id, day))}
            </div>
          )}
        </div>
      ),
    };
  });

  const rideEntries: LogEntry[] = data.rides.map((r) => {
    const category = categoryForKind("ride");
    const key = `ride:${r.id}`;
    const unconfirmed = r.ai_is_work === true && r.user_is_work === null;
    const name = `${r.service} ${r.is_cancellation ? "cancellation fee" : "ride"}${unconfirmed ? " · work?" : ""}`;
    return {
      key,
      timeIso: r.ride_time,
      category,
      node: (
        <div className="day-log-entry" key={key}>
          <DayLogRow
            category={category}
            name={name}
            meta={dayLogRowMeta(r.ride_time, r.amount)}
            interactive
            onClick={() => setOpenStrip(openStrip === key ? null : key)}
            dimmed={isDimmed(category, activeCategory)}
          />
          {openStrip === key && (
            <div className="day-log-actions">
              <button type="button" onClick={() => toggleRideWork(r)}>
                {r.is_work ? "Mark as personal" : "Mark as work"}
              </button>
              {nudgeButtons(r.auto_day, (day) => moveRide(r.id, day))}
            </div>
          )}
        </div>
      ),
    };
  });

  const socialEntries: LogEntry[] = socialRows.map((e) => {
    const isRemoved = Object.prototype.hasOwnProperty.call(removed, e.gcal_event_id);
    const category = categoryForKind("social");
    const dimmed = isDimmed(category, activeCategory);

    // §7: the ambiguity chip renders inside its own row (fixing the old
    // detached-line bug), and the "social"/"social?" chips are mutually
    // exclusive with the removed state — a removed row only ever shows
    // Removed · Undo.
    const chip = isRemoved
      ? <StatusChip kind="removed" onUndo={() => undoDidntHappen(e)} />
      : e.uncertain
      ? <StatusChip kind="uncertain" onYes={() => resolveUncertain(e, true)} onNo={() => resolveUncertain(e, false)} />
      : e.is_date
      ? <StatusChip kind="date" />
      : <StatusChip kind="social" />;

    return {
      key: `social:${e.gcal_event_id}`,
      timeIso: e.start_at,
      category,
      node: (
        <div className="day-log-entry" key={`social:${e.gcal_event_id}`}>
          <DayLogRow
            category={category}
            name={e.title}
            meta={isRemoved ? undefined : dayLogRowMeta(e.start_at, e.amount)}
            chip={chip}
            interactive={!isRemoved}
            onClick={!isRemoved ? () => openEditSocial(e) : undefined}
            dimmed={dimmed}
            removed={isRemoved}
          />
          {editingId === e.gcal_event_id && !isRemoved && (
            <div className="social-form">
              <input
                type="text"
                value={editTitle}
                onChange={(ev) => setEditTitle(ev.target.value)}
                placeholder="Event name"
                aria-label="Event name"
              />
              {/* §7: while the event is unanswered-uncertain, the checkbox
                  does not render — a checked box would assert a certainty
                  the system doesn't have. The chip above is the answer
                  surface. Once answered (or for confident events), the
                  checkbox is the correction control it always was. */}
              {!e.uncertain && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={editIsSocial}
                    onChange={(ev) => setEditIsSocial(ev.target.checked)}
                  />
                  Counts as social
                </label>
              )}
              <label className="check">
                <input
                  type="checkbox"
                  checked={editIsDate}
                  onChange={(ev) => setEditIsDate(ev.target.checked)}
                />
                Date
              </label>
              <input
                type="text"
                value={editLocation}
                onChange={(ev) => setEditLocation(ev.target.value)}
                placeholder="Where (optional)"
                aria-label="Where"
              />
              <input
                className="field-num"
                type="number"
                min="0"
                step="0.01"
                value={editAmount}
                onChange={(ev) => setEditAmount(ev.target.value)}
                placeholder="Cost"
                aria-label="Cost"
              />
              <div className="row-actions">
                <button onClick={cancelEditSocial}>Cancel</button>
                {e.source === "manual" ? (
                  <button className="danger" onClick={deleteEditSocial}>Delete</button>
                ) : (
                  <button className="danger" onClick={() => didntHappenSocial(e)}>Didn't happen</button>
                )}
                <button className="primary" onClick={saveEditSocial}>Save</button>
              </div>
            </div>
          )}
        </div>
      ),
    };
  });

  // Logged check-ins (§1: "check-ins ... in time order"; the parent spec's
  // placement rule: no time, so they sit after every timed row). Only gym
  // and alcohol map onto the closed six-category set (fitness, drink) —
  // substances has no category home among the six (§3: the cap is a rule,
  // not a suggestion), so it stays represented only by its own top-of-screen
  // toggle, unchanged.
  const checkinEntries: LogEntry[] = [];
  if (data.gym) {
    const category = categoryForKind("gym");
    checkinEntries.push({
      key: "checkin:gym",
      timeIso: null,
      category,
      node: (
        <DayLogRow
          key="checkin:gym"
          category={category}
          name="Gym"
          meta="from your check-in"
          dimmed={isDimmed(category, activeCategory)}
        />
      ),
    });
  }
  if (data.alcohol_level !== null) {
    const category = categoryForKind("alcohol");
    checkinEntries.push({
      key: "checkin:alcohol",
      timeIso: null,
      category,
      node: (
        <DayLogRow
          key="checkin:alcohol"
          category={category}
          name={`Alcohol · level ${data.alcohol_level}`}
          meta="from your check-in"
          dimmed={isDimmed(category, activeCategory)}
        />
      ),
    });
  }

  const dayLogEntries = orderDayLog([...deliveryEntries, ...rideEntries, ...socialEntries, ...checkinEntries]);
  const dayLogCategories = presentCategories(dayLogEntries);

  return (
    <div>
      <DayNav
        date={data.date}
        todayIso={todayIso ?? data.date}
        onPrev={() => setSelected(addDays(data.date, -1))}
        onNext={() => {
          const next = addDays(data.date, 1);
          setSelected(next === todayIso ? null : next);
        }}
        onPick={(iso) => setSelected(iso === todayIso ? null : iso)}
      />

      <div className="stack">
        <button className={`item${data.gym ? " done" : ""}`} onClick={toggleGym}>
          <span className="dot">{data.gym ? "✓" : ""}</span>
          <span className="txt">
            <span className="t">Gym</span>
            <span className="s">{data.gym ? "Logged — tap to undo" : "Tap to log a session"}</span>
          </span>
        </button>

        <button className={`item${data.substances ? " done" : ""}`} onClick={toggleSubstances}>
          <span className="dot">{data.substances ? "✓" : ""}</span>
          <span className="txt">
            <span className="t">Substances</span>
            <span className="s">{data.substances ? "Logged — tap to undo" : "Tap to log a day"}</span>
          </span>
        </button>

        {data.alcohol_level === null ? (
          <div className="item">
            <span className="dot"></span>
            <span className="txt">
              <span className="t">Alcohol</span>
              <span className="s">Tap a level if you drank · 1 light — 3 heavy</span>
            </span>
            <span className="chips">
              {[1, 2, 3].map((lvl) => (
                <button key={lvl} aria-label={`Log alcohol, level ${lvl} — ${LEVEL_HINTS[lvl - 1]}`} onClick={() => logAlcohol(lvl)}>
                  {lvl}
                </button>
              ))}
            </span>
          </div>
        ) : (
          <button className="item done" onClick={undoAlcohol}>
            <span className="dot num">{data.alcohol_level}</span>
            <span className="txt">
              <span className="t">Alcohol</span>
              <span className="s">Logged {LEVEL_HINTS[data.alcohol_level - 1]} — tap to undo</span>
            </span>
          </button>
        )}
      </div>

      <h2 className="section-label">Day log</h2>
      {dayLogEntries.length === 0 ? (
        <p className="day-log-empty">Nothing this day.</p>
      ) : (
        <>
          <FilterStrip categories={dayLogCategories} active={activeCategory} onSelect={setActiveCategory} />
          {dayLogEntries.map((entry) => entry.node)}
        </>
      )}

      {addingSocial ? (
        <div className="social-form">
          <input
            type="text"
            value={socialName}
            onChange={(ev) => setSocialName(ev.target.value)}
            placeholder="Event name"
            aria-label="Event name"
            autoFocus
          />
          <input
            type="text"
            value={socialLocation}
            onChange={(ev) => setSocialLocation(ev.target.value)}
            placeholder="Where (optional)"
            aria-label="Where"
          />
          <input
            className="field-num"
            type="number"
            min="0"
            step="0.01"
            value={socialAmount}
            onChange={(ev) => setSocialAmount(ev.target.value)}
            placeholder="Cost (optional)"
            aria-label="Cost"
          />
          <label className="check">
            <input
              type="checkbox"
              checked={socialIsDate}
              onChange={(ev) => setSocialIsDate(ev.target.checked)}
            />
            Date
          </label>
          <div className="row-actions">
            <button onClick={cancelAddSocial}>Cancel</button>
            <button className="primary" onClick={submitAddSocial}>Save</button>
          </div>
        </div>
      ) : (
        <button className="add-social-btn" onClick={openAddSocial}>+ Add social event</button>
      )}

      <SpendSubtotals rows={subtotalsFromDay(data)} title="Spent today" />

      {week && (
        <>
          <h2 className="section-label">
            {mondayOf(data.date) === mondayOf(todayIso ?? data.date)
              ? "This week"
              : `Week of ${weekRangeLabel(mondayOf(data.date))}`}
          </h2>
          <div className="week-strip">
            {STRIP_ORDER.map((key) => {
              const m = week.metrics[key];
              if (!m) return null;
              const ratio = m.target > 0 ? Math.min(m.count / m.target, 1) : m.count > 0 ? 1 : 0;
              const over = m.direction === "ceiling" && m.count > m.target;
              return (
                <div key={key}>
                  <span className="wl">
                    <span>{STRIP_LABELS[key]}</span>
                    <span className="num">{m.count}/{targetLabel(m.direction, m.target).slice(1)}</span>
                  </span>
                  <span className={`meter${over ? " over" : ""}`}>
                    <i style={{ width: `${(over ? 1 : ratio) * 100}%` }} />
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
