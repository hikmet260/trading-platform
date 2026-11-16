"use client";

import Link from "next/link";
import { useState } from "react";
import EquityCurve from "@/components/EquityCurve";
import { apiFetch } from "@/lib/api";

const card = "rounded-xl border border-slate-800 bg-slate-900/60 p-4";

type BacktestTrade = {
  side: string;
  entry_time: number;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  exit_reason: string;
};

type BacktestReport = {
  starting_balance: number;
  ending_balance: number;
  trade_count: number;
  win_rate_pct: number;
  profit_factor: number | string | null;
  max_drawdown_pct: number;
  total_pnl: number;
  total_return_pct: number;
  equity_curve: number[];
  trades: BacktestTrade[];
};

export default function BacktestPage() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [exchange, setExchange] = useState("binance");
  const [timeframe, setTimeframe] = useState("1m");
  const [prompt, setPrompt] = useState("Buy BTC/USDT on 1m when RSI(14) < 30 risk 0.5% stop loss 200 pips take profit 400 pips");
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true); setMessage("Loading historical candles…"); setReport(null);
    try {
      const query = new URLSearchParams({ symbol, exchange, timeframe, limit: "1000" });
      const response = await apiFetch(`/market/crypto/candles?${query}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Historical market data unavailable");
      const result = await apiFetch("/backtests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt, candles: data.candles, starting_balance: 10000, fee_bps: 5 }) });
      const reportData = await result.json();
      if (!result.ok) throw new Error(Array.isArray(reportData.detail) ? reportData.detail.join(", ") : reportData.detail ?? "Backtest failed");
      setReport(reportData as BacktestReport); setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Backtest failed"); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto min-h-screen max-w-6xl px-5 py-6 md:px-8">
    <header className="flex items-center justify-between border-b border-slate-800 pb-5"><div><p className="text-xs uppercase tracking-widest text-cyan-400">Research workbench</p><h1 className="mt-1 text-2xl font-semibold">Backtest lab</h1></div><Link href="/" className="text-sm text-cyan-300">Market terminal</Link></header>
    <section className={`${card} mt-5`}><h2 className="font-semibold">Historical simulation</h2><p className="mt-1 text-sm text-slate-400">Loads up to 1,000 recent candles from the selected exchange and evaluates the deterministic rule with adverse stop fills when intrabar order is unknown.</p>
      <div className="mt-4 grid gap-3 md:grid-cols-3"><label className="text-xs text-slate-400">Exchange<select value={exchange} onChange={(e) => setExchange(e.target.value)} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm"><option>binance</option><option>bybit</option><option>okx</option></select></label><label className="text-xs text-slate-400">Symbol<input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm"/></label><label className="text-xs text-slate-400">Timeframe<select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm">{["1m","5m","15m","30m","1h","4h","1d"].map((x) => <option key={x}>{x}</option>)}</select></label></div>
      <label className="mt-3 block text-xs text-slate-400">Strict strategy prompt<textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-3 font-mono text-sm text-slate-200"/></label><button disabled={busy} onClick={() => void run()} className="mt-3 rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold disabled:opacity-50">{busy ? "Running…" : "Run backtest"}</button>{message && <p className="mt-3 text-sm text-amber-300" role="status">{message}</p>}
    </section>
    {report && <><section className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Metric label="Trades" value={String(report.trade_count)}/><Metric label="Win rate" value={`${report.win_rate_pct.toFixed(2)}%`}/><Metric label="Profit factor" value={`${report.profit_factor ?? "—"}`}/><Metric label="Max drawdown" value={`${report.max_drawdown_pct.toFixed(2)}%`}/><Metric label="Net P&L" value={report.total_pnl.toFixed(2)}/><Metric label="Return" value={`${report.total_return_pct.toFixed(2)}%`}/></section><section className={`${card} mt-4`}><h2 className="mb-3 font-semibold">Equity curve · {report.starting_balance.toLocaleString()} → {report.ending_balance.toLocaleString()}</h2><EquityCurve values={report.equity_curve}/></section><section className={`${card} mt-4 overflow-x-auto`}><h2 className="mb-3 font-semibold">Simulated fills</h2><table className="w-full text-left text-xs"><thead className="text-slate-500"><tr>{["Side","Entry time","Entry","Exit","Quantity","P&L","Exit reason"].map((x) => <th key={x} className="p-2">{x}</th>)}</tr></thead><tbody>{report.trades.map((trade, i) => <tr key={i} className="border-t border-slate-800"><td className="p-2 uppercase">{trade.side}</td><td className="p-2">{new Date(trade.entry_time).toLocaleString()}</td><td className="p-2 font-mono">{trade.entry_price}</td><td className="p-2 font-mono">{trade.exit_price}</td><td className="p-2 font-mono">{trade.quantity.toFixed(5)}</td><td className={`p-2 font-mono ${trade.pnl >= 0 ? "text-emerald-300" : "text-red-300"}`}>{trade.pnl.toFixed(2)}</td><td className="p-2">{trade.exit_reason}</td></tr>)}</tbody></table></section></>}
  </main>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div className={card}><p className="text-xs text-slate-500">{label}</p><p className="mt-2 font-mono text-xl">{value}</p></div>; }
