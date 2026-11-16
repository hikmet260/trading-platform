"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import EquityCurve from "@/components/EquityCurve";
import { apiFetch } from "@/lib/api";

type Trade = { id: string; symbol: string; side: string; entry_price: number; exit_price: number | null; quantity: number; stop_loss: number; take_profit: number; pnl: number | null; status: string; mode: string; created_at: string };
type Analytics = { starting_equity: number; ending_equity: number; trade_count: number; win_rate_pct: number; profit_factor: number | string; curve: { equity: number }[] };

export default function TradesPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [message, setMessage] = useState("");
  const refresh = useCallback(async () => {
    try {
      const [tradesResponse, analyticsResponse] = await Promise.all([apiFetch("/paper/trades?limit=500", { cache: "no-store" }), apiFetch("/analytics/equity", { cache: "no-store" })]);
      if (!tradesResponse.ok || !analyticsResponse.ok) throw new Error("Trade history service unavailable");
      const [tradeRows, summary] = await Promise.all([tradesResponse.json(), analyticsResponse.json()]); setTrades(tradeRows); setAnalytics(summary); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not load trade history"); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function decide(trade: Trade, approved: boolean) {
    const response = await apiFetch(`/paper/trades/${trade.id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved }) });
    if (!response.ok) { setMessage("Trade is no longer pending."); return; }
    await refresh();
  }

  return <main className="mx-auto min-h-screen max-w-7xl px-5 py-6 md:px-8"><header className="flex items-center justify-between border-b border-slate-800 pb-5"><div><p className="text-xs uppercase tracking-widest text-cyan-400">Trade operations</p><h1 className="mt-1 text-2xl font-semibold">Trade history & analytics</h1></div><Link href="/" className="text-sm text-cyan-300">Market terminal</Link></header>{analytics && <><section className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Metric label="Closed trades" value={String(analytics.trade_count)}/><Metric label="Win rate" value={`${analytics.win_rate_pct.toFixed(1)}%`}/><Metric label="Profit factor" value={String(analytics.profit_factor)}/><Metric label="Paper ledger equity" value={analytics.ending_equity.toLocaleString(undefined, { maximumFractionDigits: 2 })}/></section><section className="mt-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4"><h2 className="mb-2 font-semibold">Cumulative equity curve</h2><EquityCurve values={analytics.curve.map((point) => point.equity)}/></section></>}{message && <p className="mt-3 text-sm text-amber-300">{message}</p>}<p className="mt-3 text-xs text-amber-300">Broker exits are reconciled in the background. Manual account activity and venue-specific fill details may not map cleanly to a platform trade.</p><section className="mt-4 overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/60"><table className="w-full min-w-[900px] text-left text-xs"><thead className="text-slate-500"><tr>{["Created","Mode","Market","Side","Entry","Size","Stop","Target","P&L","Status","Decision"].map((x) => <th key={x} className="p-3">{x}</th>)}</tr></thead><tbody>{trades.map((trade) => <tr key={trade.id} className="border-t border-slate-800"><td className="p-3">{new Date(trade.created_at).toLocaleString()}</td><td className="p-3 uppercase">{trade.mode}</td><td className="p-3">{trade.symbol}</td><td className="p-3 uppercase">{trade.side}</td><td className="p-3 font-mono">{trade.entry_price}</td><td className="p-3 font-mono">{trade.quantity.toFixed(5)}</td><td className="p-3 font-mono">{trade.stop_loss}</td><td className="p-3 font-mono">{trade.take_profit}</td><td className="p-3 font-mono">{trade.pnl?.toFixed(2) ?? "—"}</td><td className="p-3">{trade.status}</td><td className="flex gap-2 p-3">{trade.status === "proposed" && <><button onClick={() => void decide(trade, true)} className="rounded bg-emerald-900 px-2 py-1 text-emerald-200">{trade.mode === "live" ? "Approve live" : "Approve"}</button><button onClick={() => void decide(trade, false)} className="rounded bg-slate-800 px-2 py-1">Skip</button></>}</td></tr>)}</tbody></table>{!trades.length && !message && <p className="p-8 text-center text-sm text-slate-500">No trades recorded.</p>}</section></main>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><p className="text-xs text-slate-500">{label}</p><p className="mt-2 font-mono text-xl">{value}</p></div>; }
