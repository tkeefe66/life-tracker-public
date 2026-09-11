import { useState } from "react";
import { apiGet } from "../api";
import { money, signedMoney, signedPct } from "../lib";

interface Holding {
  symbol: string; description: string; shares: number | null;
  market_value: number; cost_basis: number | null;
  gain: number | null; gain_pct: number | null;
}
interface InvAccount {
  simplefin_id: string; name: string; market_value: number;
  cost_basis: number | null; gain: number | null; gain_pct: number | null;
  holdings: Holding[];
}
interface InvData {
  total: { market_value: number; cost_basis: number | null;
           gain: number | null; gain_pct: number | null } | null;
  accounts: InvAccount[];
}

function Gain({ gain, pct }: { gain: number | null; pct: number | null }) {
  if (gain === null) return null;
  return (
    <span className={gain < 0 ? "inv-loss" : "inv-gain"}>
      {signedMoney(gain)}{pct !== null && ` · ${signedPct(pct)}`}
    </span>
  );
}

// Live holdings vs. cost basis. Fetch-on-expand: a SimpleFIN round-trip takes
// seconds, so the Money screen never pays for it unless this section opens.
// Secondary surface: failure is a quiet inline line, never screen-level error.
// Nothing here is ever persisted — display and discard.
export default function Investments() {
  const [data, setData] = useState<InvData | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error" | "ready">("idle");

  const load = () => {
    if (state === "loading" || state === "ready") return;
    setState("loading");
    apiGet<InvData>("/bank/investments")
      .then((d) => { setData(d); setState("ready"); })
      .catch(() => setState("error"));
  };

  return (
    <details
      className="money-details"
      onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) load(); }}
    >
      <summary>Investments</summary>
      {state === "loading" && <p className="quiet">Fetching from SimpleFIN…</p>}
      {state === "error" && <p className="quiet">Couldn't reach SimpleFIN right now.</p>}
      {state === "ready" && (!data?.total || data.accounts.length === 0) && (
        <p className="quiet">No holdings reported.</p>
      )}
      {state === "ready" && data?.total && data.accounts.length > 0 && (
        <>
          <div className="inv-total">
            <span>{money(data.total.market_value)}</span>
            <Gain gain={data.total.gain} pct={data.total.gain_pct} />
          </div>
          {data.accounts.map((a) => (
            <div className="inv-acct" key={a.simplefin_id}>
              <div className="inv-acct-head">
                <span>{a.name} · {money(a.market_value)}</span>
                <Gain gain={a.gain} pct={a.gain_pct} />
              </div>
              {a.holdings.map((h, i) => (
                <div className="inv-row" key={`${h.symbol}:${h.description}:${i}`}>
                  <span className="inv-name">
                    <strong>{h.symbol}</strong>
                    {h.description && <span className="quiet"> {h.description}</span>}
                  </span>
                  <span className="inv-nums num">
                    {money(h.market_value)} <Gain gain={h.gain} pct={h.gain_pct} />
                  </span>
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </details>
  );
}
