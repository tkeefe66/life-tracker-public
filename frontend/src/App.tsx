import { useEffect, useState } from "react";
import { apiGet, LockedOutError, login, onUnauthorized, UnauthorizedError } from "./api";
import { googleAuthBroken } from "./lib";
import Today from "./screens/Today";
import Scorecard from "./screens/Scorecard";
import Money from "./screens/Money";
import Insights from "./screens/Insights";
import Settings from "./screens/Settings";

type Tab = "today" | "scorecard" | "money" | "insights" | "settings";

const TAB_META: { id: Tab; label: string; icon: JSX.Element }[] = [
  {
    id: "today",
    label: "Today",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle cx="10" cy="10" r="7.25" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="10" cy="10" r="2.5" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: "scorecard",
    label: "Week",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M4 16V9M10 16V4M16 16v-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    id: "money",
    label: "Money",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <rect x="2.5" y="5.5" width="15" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="10" cy="10" r="2.25" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
  {
    id: "insights",
    label: "Insights",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M3 14l4.5-5 3 3L17 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="17" cy="5" r="1.4" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: "settings",
    label: "Settings",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M3 6h9M15.5 6H17M3 14h2.5M9 14h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="13.5" cy="6" r="1.75" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="6.5" cy="14" r="1.75" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
];

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (await login(password)) onLogin();
      else setError("Wrong password");
    } catch (err) {
      if (err instanceof LockedOutError) setError("Too many attempts — try again shortly.");
      else setError("Can't reach the server.");
    }
  };
  return (
    <form className="login" onSubmit={submit}>
      <h1 className="wordmark">On Track</h1>
      <input
        type="password"
        value={password}
        placeholder="Password"
        onChange={(e) => setPassword(e.target.value)}
        autoFocus
      />
      <button type="submit">Sign in</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("today");
  // A date tapped on Week's day-by-day view, carried to Today and consumed
  // once it lands there — see Today's initialDate/onConsumed.
  const [pendingDay, setPendingDay] = useState<string | null>(null);
  // App-wide "Google is disconnected" banner (CLAUDE.md: this must surface
  // on every screen, not just Settings). A secondary surface — a failed
  // fetch leaves this false, no banner, no screen-level error, page never
  // blanks.
  const [googleBroken, setGoogleBroken] = useState(false);

  const probe = () => {
    setProbeError(null);
    apiGet("/targets")
      .then(() => setAuthed(true))
      .catch((e) => {
        if (e instanceof UnauthorizedError) setAuthed(false);
        else setProbeError("Can't reach the server.");
      });
  };

  useEffect(() => {
    probe();
  }, []);

  // Any screen's fetch can hit a 401 once a session genuinely expires or is
  // revoked elsewhere — not just this initial probe.
  useEffect(() => {
    onUnauthorized(() => setAuthed(false));
  }, []);

  // Fetched once per app load (on the authed transition), not on every tab
  // switch — Settings.tsx does its own richer fetch/banner independently.
  useEffect(() => {
    if (!authed) return;
    apiGet<{ gmail_last_status: string | null; calendar_last_status: string | null }>("/settings")
      .then((s) => setGoogleBroken(googleAuthBroken(s.gmail_last_status, s.calendar_last_status)))
      .catch(() => {});
  }, [authed]);

  if (probeError) {
    return (
      <div className="center">
        <p className="error">{probeError}</p>
        <button onClick={probe}>Retry</button>
      </div>
    );
  }

  if (authed === null) return <p className="center">Loading…</p>;
  if (!authed) return <LoginScreen onLogin={() => setAuthed(true)} />;

  return (
    <div className="app">
      <main>
        {googleBroken && (
          <div className="banner">
            Google is disconnected — passive tracking is paused. See Settings.
          </div>
        )}
        {tab === "today" && (
          <Today initialDate={pendingDay} onConsumed={() => setPendingDay(null)} />
        )}
        {tab === "scorecard" && (
          <Scorecard onOpenDay={(iso) => { setPendingDay(iso); setTab("today"); }} />
        )}
        {tab === "money" && <Money />}
        {tab === "insights" && <Insights />}
        {tab === "settings" && <Settings onLoggedOut={() => setAuthed(false)} />}
      </main>
      <nav className="tabs">
        {TAB_META.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "active" : ""}
            aria-current={tab === t.id ? "page" : undefined}
            onClick={() => setTab(t.id)}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
