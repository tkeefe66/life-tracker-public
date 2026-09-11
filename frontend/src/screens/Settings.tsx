import { useEffect, useState } from "react";
import { apiGet, apiSend, logout, logoutAll } from "../api";
import { targetLabel } from "../lib";

interface Target { direction: string; value: number }
interface SettingsData {
  telegram_push: "on" | "off";
  telegram_configured: boolean;
  google_configured: boolean;
  gmail_last_run: string | null;
  gmail_last_status: string | null;
  gmail_last_result: string | null;
  calendar_last_run: string | null;
  calendar_last_status: string | null;
  backup_last_run: string | null;
  backup_last_status: string | null;
  bank_last_run: string | null;
  bank_last_status: string | null;
  bank_last_result: string | null;
}

interface Delivery { service: string; subject: string; ordered_at: string; amount: number | null }

interface BankAccount {
  simplefin_id: string;
  name: string;
  org: string;
  kind: string;
  role: string;
  active: boolean;
  last_synced_at: string | null;
  nickname: string | null;
  display_name: string;
}

const BANK_ROLES = ["spending", "bills", "savings", "investment", "credit_card", "unknown"] as const;

function orderDate(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

const LABELS: Record<string, string> = {
  gym: "Gym sessions",
  social: "Social events",
  delivery: "Delivery orders",
  alcohol: "Alcohol days",
  substances: "Substances",
};

function statusLine(status: string | null, run: string | null): string {
  if (!status) return "Hasn't run yet";
  const when = run ? new Date(run) : null;
  const at = when && !isNaN(when.getTime())
    ? when.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "";
  return status === "ok" ? `OK${at ? ` · ${at}` : ""}` : status;
}

export default function Settings({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [targets, setTargets] = useState<Record<string, Target> | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[] | null>(null);
  const [bankAccounts, setBankAccounts] = useState<BankAccount[] | null>(null);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    apiGet<Record<string, Target>>("/targets").then(setTargets).catch((e) => setError(e.message));
    apiGet<SettingsData>("/settings").then(setSettings).catch((e) => setError(e.message));
    apiGet<{ orders: Delivery[] }>("/deliveries?days=60")
      .then((r) => setDeliveries(r.orders))
      .catch(() => setDeliveries(null));
    apiGet<BankAccount[]>("/bank/accounts")
      .then(setBankAccounts)
      .catch(() => setBankAccounts(null));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!targets || !settings) return <p className="center">Loading…</p>;

  const googleBroken =
    !settings.google_configured ||
    settings.gmail_last_status?.startsWith("error") ||
    settings.calendar_last_status?.startsWith("error");

  const updateTarget = async (metric: string, value: number) => {
    setSaveError("");
    if (Number.isNaN(value) || value < 0 || !Number.isInteger(value)) return;
    try {
      const updated = await apiSend<Record<string, Target>>("PUT", "/targets", { [metric]: value });
      setTargets(updated);
    } catch (e) {
      setSaveError((e as Error).message);
    }
  };

  const signOut = async () => {
    await logout();
    onLoggedOut();
  };

  const signOutEverywhere = async () => {
    // Confirm because this is destructive and effectively irreversible: it
    // kills sessions on devices the user may not have in hand, and the only
    // way back is re-entering the password on each one. It sits directly
    // below the ordinary Sign out button, so a misclick is plausible.
    if (!window.confirm("Sign out on every device? You'll need to log in again everywhere.")) return;
    await logoutAll();
    onLoggedOut();
  };

  const updateRole = async (simplefinId: string, role: string) => {
    setSaveError("");
    const prev = bankAccounts;
    setBankAccounts((accts) =>
      accts ? accts.map((a) => (a.simplefin_id === simplefinId ? { ...a, role } : a)) : accts
    );
    try {
      await apiSend("POST", `/bank/accounts/${simplefinId}/role`, { role });
    } catch (e) {
      setBankAccounts(prev);
      setSaveError((e as Error).message);
    }
  };

  const updateNickname = async (simplefinId: string, raw: string) => {
    setSaveError("");
    const nickname = raw.trim();
    const prev = bankAccounts;
    const current = prev?.find((a) => a.simplefin_id === simplefinId);
    if (!current || (current.nickname ?? "") === nickname) return;
    setBankAccounts((accts) =>
      accts ? accts.map((a) => (a.simplefin_id === simplefinId
        ? { ...a, nickname: nickname || null, display_name: nickname || a.name }
        : a)) : accts
    );
    try {
      await apiSend("POST", `/bank/accounts/${simplefinId}/nickname`, { nickname });
    } catch (e) {
      setBankAccounts(prev);
      setSaveError((e as Error).message);
    }
  };

  const togglePush = async () => {
    setSaveError("");
    const next = settings.telegram_push === "on" ? "off" : "on";
    try {
      await apiSend("PUT", "/settings", { telegram_push: next });
      setSettings({ ...settings, telegram_push: next });
    } catch (e) {
      setSaveError((e as Error).message);
    }
  };

  return (
    <div>
      <div className="screen-head">
        <h2>Settings</h2>
      </div>

      {saveError && <p className="error">{saveError}</p>}
      {googleBroken && (
        <div className="banner">
          Google is disconnected, so passive tracking is degraded. Re-run{" "}
          <code>python scripts/calendar_auth.py</code> and update the refresh token.
        </div>
      )}

      <h2 className="section-label">Weekly targets</h2>
      <div className="group">
        {Object.keys(LABELS).map((key) => (
          <label className="row" key={key}>
            <span className="grow">
              {LABELS[key]}
              <span className="hint">
                {targets[key].direction === "ceiling" ? "At most" : "At least"}{" "}
                {targetLabel(targets[key].direction, targets[key].value).slice(1)} per week
              </span>
            </span>
            <input
              className="field-num"
              type="number" min={0} step={1} value={targets[key].value}
              onChange={(e) => updateTarget(key, Number(e.target.value))}
            />
          </label>
        ))}
      </div>

      <h2 className="section-label">Weekly summary</h2>
      <div className="group">
        {settings.telegram_configured ? (
          <label className="row">
            <span className="grow">
              Telegram push
              <span className="hint">One scorecard message, Monday mornings</span>
            </span>
            <span className="switch">
              <input
                type="checkbox"
                checked={settings.telegram_push === "on"}
                onChange={togglePush}
                aria-label="Telegram weekly push"
              />
              <span className="knob" />
            </span>
          </label>
        ) : (
          <div className="row">
            <span className="grow">
              Telegram push
              <span className="hint">Not configured — TELEGRAM_BOT_TOKEN is unset</span>
            </span>
          </div>
        )}
      </div>

      <h2 className="section-label">Sync</h2>
      <div className="group">
        <div className="row">
          <span className="grow">
            Gmail receipts
            <span className="hint">
              {statusLine(settings.gmail_last_status, settings.gmail_last_run)}
              {settings.gmail_last_result ? ` · ${settings.gmail_last_result}` : ""}
            </span>
          </span>
        </div>
        <div className="row">
          <span className="grow">
            Calendar events
            <span className="hint">{statusLine(settings.calendar_last_status, settings.calendar_last_run)}</span>
          </span>
        </div>
        <div className="row">
          <span className="grow">
            Backups
            <span className="hint">
              {settings.backup_last_status
                ? statusLine(settings.backup_last_status, settings.backup_last_run)
                : "Not configured — BACKUP_S3_* is unset"}
            </span>
          </span>
        </div>
        <div className="row">
          <span className="grow">
            Bank sync
            <span className="hint">
              {settings.bank_last_status ? (
                <>
                  {statusLine(settings.bank_last_status, settings.bank_last_run)}
                  {settings.bank_last_result ? ` · ${settings.bank_last_result}` : ""}
                </>
              ) : (
                "Not set up"
              )}
            </span>
          </span>
        </div>
      </div>

      {bankAccounts && bankAccounts.length > 0 && (
        <>
          <h2 className="section-label">Bank accounts</h2>
          <div className="group">
            {bankAccounts.map((a) => (
              <label className="row" key={a.simplefin_id}>
                <span className="grow">{a.org} — {a.name}</span>
                {/* keyed to the saved value: a failed save's rollback must remount the input, or it shows stale text and re-POSTs on next blur */}
                <input
                  type="text"
                  className="nickname-input"
                  aria-label="Nickname"
                  placeholder="Nickname"
                  defaultValue={a.nickname ?? ""}
                  key={`${a.simplefin_id}:${a.nickname ?? ""}`}
                  onBlur={(e) => updateNickname(a.simplefin_id, e.currentTarget.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
                />
                <select
                  aria-label="Account role"
                  value={a.role}
                  onChange={(e) => updateRole(a.simplefin_id, e.target.value)}
                >
                  {BANK_ROLES.map((role) => (
                    <option key={role} value={role}>{role}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <p className="footnote">
            Roles decide what counts as spending. A change takes effect on the next sync.
          </p>
        </>
      )}

      {deliveries && (
        <>
          <h2 className="section-label">Detected orders</h2>
          <details className="orders">
            <summary>
              {deliveries.length === 0
                ? "None detected yet"
                : `${deliveries.length} in the last 60 days`}
            </summary>
            {deliveries.map((o) => (
              <p className="quiet" key={o.ordered_at + o.subject}>
                <span>{o.service} — {o.subject}</span>
                <span className="when">
                  {o.amount != null && `$${o.amount.toFixed(2).replace(/\.00$/, "")} · `}
                  {orderDate(o.ordered_at)}
                </span>
              </p>
            ))}
          </details>
        </>
      )}

      <h2 className="section-label">Account</h2>
      <div className="group">
        <div className="row">
          <span className="grow">Sign out</span>
          <button type="button" onClick={signOut}>Sign out</button>
        </div>
        <div className="row">
          <span className="grow">Sign out everywhere</span>
          <button type="button" onClick={signOutEverywhere}>Sign out everywhere</button>
        </div>
      </div>
    </div>
  );
}
