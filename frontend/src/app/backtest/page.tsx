"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/Navbar";
import { apiFetch, formatErrorDetail } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { BacktestPageSkeleton } from "@/components/ui/PageSkeletons";
import { startTopLoader, stopTopLoader } from "@/components/ui/TopProgressBar";
import { DataTablePagination } from "@/components/ui/DataTablePagination";

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
  return (
    <Suspense fallback={<BacktestPageSkeleton />}>
      <BacktestContent />
    </Suspense>
  );
}

function BacktestContent() {
  const { toast } = useToast();
  const searchParams = useSearchParams();
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [exchange, setExchange] = useState("binance");
  const [timeframe, setTimeframe] = useState("1m");
  const [prompt, setPrompt] = useState(
    "Buy BTC/USDT on 1m when RSI(14) < 30 risk 0.5% stop loss 200 pips take profit 400 pips"
  );
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [tradePage, setTradePage] = useState(1);
  const [tradePageSize, setTradePageSize] = useState(20);

  useEffect(() => {
    const qPrompt = searchParams.get("prompt");
    const qSymbol = searchParams.get("symbol");
    const qTimeframe = searchParams.get("timeframe");
    if (qPrompt) setPrompt(qPrompt);
    if (qSymbol) setSymbol(qSymbol);
    if (qTimeframe) setTimeframe(qTimeframe);
  }, [searchParams]);

  async function run() {
    setBusy(true);
    startTopLoader("Running Simulation...");
    setMessage("Loading historical candles & running simulation...");
    setReport(null);
    try {
      const query = new URLSearchParams({ symbol, exchange, timeframe, limit: "1000" });
      const response = await apiFetch(`/market/crypto/candles?${query}`);
      const data = await response.json();
      if (!response.ok) throw new Error(formatErrorDetail(data.detail, "Historical market data unavailable"));
      const result = await apiFetch("/backtests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt,
          candles: data.candles,
          starting_balance: 10000,
          fee_bps: 5,
        }),
      });
      const reportData = await result.json();
      if (!result.ok) throw new Error(formatErrorDetail(reportData.detail, "Backtest failed"));
      setReport(reportData as BacktestReport);
      setTradePage(1);
      setMessage("");
      toast.success(
        `Simulation finished: ${reportData.trade_count} trades (${reportData.win_rate_pct.toFixed(1)}% win rate)`,
        "Backtest Succeeded"
      );
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : "Backtest failed";
      setMessage(errMsg);
      toast.error(errMsg, "Backtest Error");
    } finally {
      setBusy(false);
      stopTopLoader();
    }
  }

  const totalTradePages = Math.ceil((report?.trades?.length || 0) / tradePageSize) || 1;
  const paginatedBacktestTrades = (report?.trades || []).slice(
    (tradePage - 1) * tradePageSize,
    tradePage * tradePageSize
  );

  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Backtest Lab</h1>
          <p className="mt-0.5 text-xs text-zinc-400">
            Historical simulation with deterministic execution modeling
          </p>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <Link href="/" className="text-cyan-400 hover:text-cyan-300 transition-colors">
            ← Terminal Workspace
          </Link>
        </div>
      </header>

      <section className="mt-5 rounded-xl border border-[#27272a] bg-[#18181b] p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
          Simulation Setup
        </h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <div>
            <label className="block text-[11px] font-medium text-zinc-400 mb-1">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
              className="w-full rounded border border-[#27272a] bg-[#141416] p-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
            >
              <option value="binance">Binance</option>
              <option value="bybit">Bybit</option>
              <option value="okx">OKX</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] font-medium text-zinc-400 mb-1">Symbol</label>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="w-full rounded border border-[#27272a] bg-[#141416] p-2 text-xs font-mono text-zinc-200 outline-none uppercase focus:border-cyan-500"
            />
          </div>
          <div>
            <label className="block text-[11px] font-medium text-zinc-400 mb-1">Timeframe</label>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="w-full rounded border border-[#27272a] bg-[#141416] p-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
            >
              {["1m", "5m", "15m", "30m", "1h", "4h", "1d"].map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-3">
          <label className="block text-[11px] font-medium text-zinc-400 mb-1">
            Strategy Rule Prompt
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            className="w-full rounded border border-[#27272a] bg-[#141416] p-3 font-mono text-xs text-zinc-200 outline-none focus:border-cyan-500"
          />
        </div>

        <div className="mt-4 flex items-center justify-between">
          <button
            disabled={busy}
            onClick={() => void run()}
            className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-4 py-2 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer"
          >
            {busy ? "Running Simulation..." : "Run Historical Backtest"}
          </button>
          {message && <span className="text-xs text-amber-300">{message}</span>}
        </div>
      </section>

      {report && (
        <>
          {/* Executive Simulation KPI Cards */}
          <section className="mt-4 grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
            <Metric label="Trades Executed" value={String(report.trade_count)} />
            <Metric
              label="Win Rate"
              value={`${report.win_rate_pct.toFixed(1)}%`}
              highlight={report.win_rate_pct >= 50 ? "emerald" : "rose"}
            />
            <Metric label="Profit Factor" value={String(report.profit_factor ?? "—")} />
            <Metric
              label="Max Drawdown"
              value={`${report.max_drawdown_pct.toFixed(2)}%`}
              highlight={report.max_drawdown_pct > 15 ? "rose" : undefined}
            />
            <Metric
              label="Net P&L"
              value={`${report.total_pnl >= 0 ? "+" : ""}$${report.total_pnl.toFixed(2)} (${report.total_return_pct.toFixed(2)}%)`}
              highlight={report.total_pnl >= 0 ? "emerald" : "rose"}
            />
            <Metric
              label="Final Balance"
              value={`$${report.ending_balance.toLocaleString(undefined, {
                maximumFractionDigits: 2,
              })}`}
              highlight="cyan"
            />
          </section>

          {/* Trade Execution Ledger Table */}
          <section className="mt-4 rounded-xl border border-[#27272a] bg-[#18181b] overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-left text-xs font-mono">
              <thead className="border-b border-[#26262b] bg-[#151518] text-[11px] font-sans text-zinc-400">
                <tr>
                  {[
                    "Side",
                    "Entry Time",
                    "Entry Price",
                    "Exit Price",
                    "Quantity",
                    "P&L",
                    "Exit Reason",
                  ].map((x) => (
                    <th key={x} className="p-3">
                      {x}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#26262b]/60 text-zinc-300">
                {paginatedBacktestTrades.map((trade, i) => (
                  <tr key={i} className="hover:bg-[#1f1f24] transition-colors">
                    <td className="p-3">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[11px] font-mono font-medium ${
                          trade.side.toLowerCase() === "buy"
                            ? "text-emerald-400 bg-emerald-950/40 border border-emerald-500/30"
                            : "text-rose-400 bg-rose-950/40 border border-rose-500/30"
                        }`}
                      >
                        {trade.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="p-3 text-zinc-400 font-sans">
                      {new Date(trade.entry_time).toLocaleString()}
                    </td>
                    <td className="p-3">{trade.entry_price}</td>
                    <td className="p-3">{trade.exit_price}</td>
                    <td className="p-3">{trade.quantity.toFixed(5)}</td>
                    <td
                      className={`p-3 font-medium ${
                        trade.pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {trade.pnl.toFixed(2)}
                    </td>
                    <td className="p-3 text-zinc-400 font-sans">{trade.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>

              {report.trades.length === 0 && (
                <div className="p-8 text-center text-xs text-zinc-500">
                  No trades executed during this simulation period.
                </div>
              )}
            </div>

            <DataTablePagination
              currentPage={tradePage}
              totalPages={totalTradePages}
              pageSize={tradePageSize}
              totalItems={report.trades.length}
              onPageChange={setTradePage}
              onPageSizeChange={(newSize) => {
                setTradePageSize(newSize);
                setTradePage(1);
              }}
              itemName="simulated trades"
            />
          </section>
        </>
      )}
    </main>
    </div>
  );
}

function Metric({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: "emerald" | "rose" | "cyan";
}) {
  return (
    <div className="rounded-xl border border-[#27272a] bg-[#18181b] p-4 transition-all hover:border-[#383842]">
      <p className="text-[11px] text-zinc-400 font-medium tracking-tight">{label}</p>
      <p
        className={`mt-1.5 font-mono text-xl font-semibold tracking-tight ${
          highlight === "emerald"
            ? "text-emerald-400"
            : highlight === "rose"
            ? "text-rose-400"
            : highlight === "cyan"
            ? "text-cyan-400"
            : "text-zinc-100"
        }`}
      >
        {value}
      </p>
    </div>
  );
}
