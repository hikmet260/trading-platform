"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

type Strategy = { id: string; name: string; prompt: string; rule: Record<string, unknown>; enabled: boolean; broker_account_id: string | null; created_at: string };

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try { const response = await apiFetch("/strategies", { cache: "no-store" }); if (!response.ok) throw new Error("Could not load saved strategies"); setStrategies(await response.json()); }
    catch (e) { setError(e instanceof Error ? e.message : "API unavailable"); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function toggle(item: Strategy) {
    const response = await apiFetch(`/strategies/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !item.enabled }) });
    if (!response.ok) { setError("Strategy update failed"); return; }
    await refresh();
  }

  return <main className="mx-auto min-h-screen max-w-6xl px-5 py-6 md:px-8"><header className="flex items-center justify-between border-b border-slate-800 pb-5"><div><p className="text-xs uppercase tracking-widest text-cyan-400">Strategy studio</p><h1 className="mt-1 text-2xl font-semibold">Saved strategies</h1></div><Link href="/" className="text-sm text-cyan-300">Market terminal</Link></header><div className="mt-5 flex gap-3"><Link href="/" className="rounded-lg bg-cyan-800 px-4 py-2 text-sm">Create strategy</Link><Link href="/accounts" className="rounded-lg border border-slate-700 px-4 py-2 text-sm">Broker accounts</Link><Link href="/backtest" className="rounded-lg border border-slate-700 px-4 py-2 text-sm">Open backtest lab</Link></div>{error && <p className="mt-3 text-sm text-amber-300">{error}</p>}<section className="mt-4 space-y-3">{strategies.length === 0 && !error && <div className="rounded-xl border border-slate-800 p-8 text-center text-sm text-slate-400">No strategies saved. Compile and save one from the market terminal.</div>}{strategies.map((strategy) => <article key={strategy.id} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">{strategy.name}</h2><p className="mt-1 text-xs text-slate-500">Saved {new Date(strategy.created_at).toLocaleString()} · {strategy.broker_account_id ? `Account ${strategy.broker_account_id}` : "Paper account"}</p></div><button onClick={() => void toggle(strategy)} className={`rounded-lg px-3 py-2 text-xs ${strategy.enabled ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-300"}`}>{strategy.enabled ? "Enabled · disable" : "Disabled · enable"}</button></div><p className="mt-4 rounded bg-slate-950 p-3 font-mono text-sm text-slate-300">{strategy.prompt}</p><pre className="mt-3 overflow-x-auto rounded bg-slate-950 p-3 text-xs text-cyan-200">{JSON.stringify(strategy.rule, null, 2)}</pre></article>)}</section></main>;
}
