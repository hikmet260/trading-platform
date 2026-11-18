"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, ArrowUpRight, ArrowDownRight } from "lucide-react";
import Navbar from "@/components/Navbar";
import { apiFetch, formatErrorDetail } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { useTheme } from "@/context/ThemeContext";
import { TradesPageSkeleton } from "@/components/ui/PageSkeletons";
import { startTopLoader, stopTopLoader } from "@/components/ui/TopProgressBar";
import { DataTablePagination } from "@/components/ui/DataTablePagination";

type Trade = {
  id: string;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number | null;
  quantity: number;
  stop_loss: number;
  take_profit: number;
  pnl: number | null;
  status: string;
  mode: string;
  created_at: string;
};

type Analytics = {
  starting_equity: number;
  ending_equity: number;
  trade_count: number;
  win_rate_pct: number;
  profit_factor: number | string;
  curve: { equity: number }[];
};

export default function TradesPage() {
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const { theme } = useTheme();
  const isLight = theme === "light";

  const [trades, setTrades] = useState<Trade[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Filters
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "open" | "closed" | "proposed">("all");
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    startTopLoader("Syncing Trades...");
    try {
      const [tradesResponse, analyticsResponse] = await Promise.all([
        apiFetch("/paper/trades?limit=500", { cache: "no-store" }),
        apiFetch("/analytics/equity", { cache: "no-store" }),
      ]);
      if (!tradesResponse.ok || !analyticsResponse.ok)
        throw new Error("Trade history service unavailable");
      const [tradeRows, summary] = await Promise.all([
        tradesResponse.json(),
        analyticsResponse.json(),
      ]);
      setTrades(tradeRows);
      setAnalytics(summary);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load trade history");
    } finally {
      setLoading(false);
      setRefreshing(false);
      stopTopLoader();
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function decide(trade: Trade, approved: boolean) {
    const actionWord = approved ? "Approve" : "Reject";
    const confirmed = await confirm({
      title: `${actionWord} ${trade.side.toUpperCase()} Order for ${trade.symbol}?`,
      description: approved
        ? `Confirming execution will route this ${trade.mode.toUpperCase()} order to the broker ledger at current market price.`
        : "Rejecting this proposed order will drop it from the pending execution queue.",
      confirmLabel: approved ? "Execute Order" : "Reject Order",
      cancelLabel: "Cancel",
      variant: approved ? "primary" : "danger",
      details: [
        { label: "Market", value: trade.symbol },
        { label: "Direction", value: trade.side.toUpperCase(), badge: trade.side === "buy" ? "emerald" : "red" },
        { label: "Entry Price", value: `$${trade.entry_price}` },
        { label: "Position Size", value: `${trade.quantity} units` },
        { label: "Stop Loss", value: trade.stop_loss ? `$${trade.stop_loss}` : "None" },
        { label: "Take Profit", value: trade.take_profit ? `$${trade.take_profit}` : "None" },
      ],
    });

    if (!confirmed) return;

    const response = await apiFetch(`/paper/trades/${trade.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    if (!response.ok) {
      const data = (await response.json().catch(() => ({}))) as { detail?: unknown };
      const err = formatErrorDetail(data.detail, "Trade is no longer pending.");
      setMessage(err);
      toast.error(err, "Decision Rejected");
      return;
    }
    toast.info(
      `Trade ${trade.symbol} (${trade.side.toUpperCase()}) was ${approved ? "Approved" : "Rejected"}`,
      "Decision Recorded"
    );
    await refresh();
  }

  // Counts
  const pendingTrades = useMemo(() => trades.filter((t) => t.status === "proposed"), [trades]);
  const closedCount = useMemo(() => trades.filter((t) => t.status === "closed").length, [trades]);
  const openCount = useMemo(() => trades.filter((t) => t.status === "open").length, [trades]);

  const filteredTrades = useMemo(() => {
    return trades.filter((t) => {
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return (
        t.symbol.toLowerCase().includes(q) ||
        t.side.toLowerCase().includes(q) ||
        t.mode.toLowerCase().includes(q)
      );
    });
  }, [trades, statusFilter, searchQuery]);

  // Reset to first page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, searchQuery]);

  const totalPages = Math.ceil(filteredTrades.length / pageSize) || 1;
  const paginatedTrades = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredTrades.slice(start, start + pageSize);
  }, [filteredTrades, currentPage, pageSize]);

  if (loading) {
    return <TradesPageSkeleton />;
  }

  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
          <div>
            <h1 className="text-xl font-semibold text-zinc-100">Trade Operations & History</h1>
            <p className="mt-0.5 text-xs text-zinc-400">
              Audit ledger, execution verification, and equity analytics
            </p>
          </div>
          <div className="flex items-center gap-4 text-xs">
            <Link href="/" className="text-cyan-400 hover:text-cyan-300 transition-colors">
              ← Terminal Workspace
            </Link>
          </div>
        </header>

        {/* Action Required: Pending Orders Banner */}
        {pendingTrades.length > 0 && (
          <section className="mt-4 rounded-xl border border-amber-500/30 bg-amber-950/20 p-4">
            <div className="flex items-center justify-between pb-2 border-b border-amber-500/20">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                <h2 className="text-xs font-semibold uppercase tracking-wider text-amber-300">
                  Pending Approvals ({pendingTrades.length})
                </h2>
              </div>
              <span className="text-[11px] text-amber-400/80">Semi-Auto Execution Gate</span>
            </div>
            <div className="mt-3 divide-y divide-amber-500/10">
              {pendingTrades.map((trade) => (
                <div
                  key={trade.id}
                  className="py-2.5 flex flex-wrap items-center justify-between gap-3 text-xs"
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono font-bold ${
                        trade.side === "buy"
                          ? "bg-emerald-950/60 text-emerald-400 border border-emerald-500/30"
                          : "bg-red-950/60 text-red-400 border border-red-500/30"
                      }`}
                    >
                      {trade.side === "buy" ? (
                        <ArrowUpRight className="w-3 h-3" />
                      ) : (
                        <ArrowDownRight className="w-3 h-3" />
                      )}
                      {trade.side.toUpperCase()}
                    </span>
                    <span className="font-semibold text-zinc-100 font-mono">{trade.symbol}</span>
                    <span className="text-zinc-400 font-mono">Entry: ${trade.entry_price}</span>
                    <span className="text-zinc-400 font-mono">Size: {trade.quantity}</span>
                    {trade.stop_loss ? (
                      <span className="text-red-400 font-mono">SL: ${trade.stop_loss}</span>
                    ) : null}
                    {trade.take_profit ? (
                      <span className="text-emerald-400 font-mono">TP: ${trade.take_profit}</span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void decide(trade, true)}
                      className="rounded bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white transition-colors cursor-pointer"
                    >
                      Approve & Execute
                    </button>
                    <button
                      type="button"
                      onClick={() => void decide(trade, false)}
                      className="rounded border border-[#27272a] bg-[#141416] hover:bg-[#222227] px-3 py-1.5 text-xs text-zinc-300 transition-colors cursor-pointer"
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Analytics KPI Metrics & Equity Curve */}
        {analytics && (
          <>
            <section className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Metric label="Closed Trades" value={String(analytics.trade_count)} />
              <Metric label="Win Rate" value={`${analytics.win_rate_pct.toFixed(1)}%`} />
              <Metric label="Profit Factor" value={String(analytics.profit_factor)} />
              <Metric
                label="Ledger Equity"
                value={`$${analytics.ending_equity.toLocaleString(undefined, {
                  maximumFractionDigits: 2,
                })}`}
              />
            </section>
          </>
        )}

        {/* Toolbar & Filters */}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setStatusFilter("all")}
              className={`px-3 py-1 rounded-md font-medium transition-colors cursor-pointer ${
                statusFilter === "all"
                  ? "bg-[#27272a] text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              All ({trades.length})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("open")}
              className={`px-3 py-1 rounded-md font-medium transition-colors cursor-pointer ${
                statusFilter === "open"
                  ? "bg-[#27272a] text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Open ({openCount})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("closed")}
              className={`px-3 py-1 rounded-md font-medium transition-colors cursor-pointer ${
                statusFilter === "closed"
                  ? "bg-[#27272a] text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Closed ({closedCount})
            </button>
            {pendingTrades.length > 0 && (
              <button
                type="button"
                onClick={() => setStatusFilter("proposed")}
                className={`px-3 py-1 rounded-md font-medium transition-colors cursor-pointer ${
                  statusFilter === "proposed"
                    ? "bg-amber-500 text-zinc-950 font-semibold"
                    : "text-amber-400 hover:text-amber-300"
                }`}
              >
                Pending ({pendingTrades.length})
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 w-full sm:w-auto">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter by symbol..."
              className="w-full sm:w-60 rounded border border-[#27272a] bg-[#141416] px-2.5 py-1.5 text-xs text-zinc-200 outline-none focus:border-cyan-500"
            />
            <button
              type="button"
              onClick={() => void refresh()}
              title="Refresh trade ledger"
              className="p-2 rounded border border-[#27272a] bg-[#141416] hover:bg-[#222227] text-zinc-400 hover:text-zinc-200 transition-colors cursor-pointer shrink-0"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>

        {message && <p className="mt-3 text-xs text-amber-400">{message}</p>}

        {/* Ledger Table */}
        <section className="mt-3 rounded-xl border border-[#27272a] bg-[#18181b] overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs font-mono">
            <thead className="border-b border-[#26262b] bg-[#151518] text-[11px] font-sans text-zinc-400">
              <tr>
                {[
                  "Timestamp",
                  "Mode",
                  "Market",
                  "Side",
                  "Entry",
                  "Size",
                  "Stop Loss",
                  "Take Profit",
                  "Realized P&L",
                  "Status",
                  "Action",
                ].map((x) => (
                  <th key={x} className="p-3">
                    {x}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[#26262b]/60 text-zinc-300">
              {paginatedTrades.map((trade) => {
                const isBuy = trade.side.toLowerCase() === "buy";
                const pnl = trade.pnl;

                return (
                  <tr key={trade.id} className="hover:bg-[#1f1f24] transition-colors">
                    <td className="p-3 text-zinc-400 font-sans whitespace-nowrap">
                      {new Date(trade.created_at).toLocaleString([], {
                        month: "short",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </td>
                    <td className="p-3">
                      <span
                        className={`inline-block text-[10px] font-mono uppercase px-2 py-0.5 rounded border transition-colors ${getModeBadgeClasses(
                          trade.mode,
                          isLight
                        )}`}
                      >
                        {trade.mode}
                      </span>
                    </td>
                    <td className="p-3 font-semibold text-zinc-100 font-mono">{trade.symbol}</td>
                    <td className="p-3">
                      <span
                        className={`font-semibold ${
                          isBuy ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {trade.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="p-3">${trade.entry_price.toLocaleString()}</td>
                    <td className="p-3">{trade.quantity.toFixed(5)}</td>
                    <td className="p-3 text-zinc-400">
                      {trade.stop_loss ? `$${trade.stop_loss.toLocaleString()}` : "—"}
                    </td>
                    <td className="p-3 text-zinc-400">
                      {trade.take_profit ? `$${trade.take_profit.toLocaleString()}` : "—"}
                    </td>
                    <td className="p-3 font-medium">
                      {pnl !== null ? (
                        <span className={pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>
                          {pnl >= 0 ? "+" : ""}
                          {pnl.toFixed(2)}
                        </span>
                      ) : (
                        <span className="text-zinc-500">—</span>
                      )}
                    </td>
                    <td className="p-3">
                      <span
                        className={`text-[11px] px-2.5 py-0.5 rounded-full border capitalize font-medium ${getStatusBadgeClasses(
                          trade.status,
                          isLight
                        )}`}
                      >
                        {trade.status}
                      </span>
                    </td>
                    <td className="p-3">
                      {trade.status === "proposed" ? (
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => void decide(trade, true)}
                            className="px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-medium transition-colors"
                          >
                            Execute
                          </button>
                          <button
                            type="button"
                            onClick={() => void decide(trade, false)}
                            className="px-2 py-1 rounded border border-[#27272a] bg-[#141416] hover:bg-[#222227] text-zinc-300 text-[11px] transition-colors"
                          >
                            Skip
                          </button>
                        </div>
                      ) : (
                        <span className="text-zinc-500 text-xs">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filteredTrades.length === 0 && (
            <div className="p-8 text-center text-xs text-zinc-500">
              No trades found matching current filter.
            </div>
          )}
          </div>
          <DataTablePagination
            currentPage={currentPage}
            totalPages={totalPages}
            pageSize={pageSize}
            totalItems={filteredTrades.length}
            onPageChange={setCurrentPage}
            onPageSizeChange={(newSize) => {
              setPageSize(newSize);
              setCurrentPage(1);
            }}
            itemName="trades"
          />
        </section>
      </main>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[#27272a] bg-[#18181b] p-4">
      <p className="text-[11px] uppercase tracking-wider text-zinc-400 font-sans">{label}</p>
      <p className="mt-1.5 font-mono text-xl font-bold text-zinc-100">{value}</p>
    </div>
  );
}

function getModeBadgeClasses(mode: string, isLight: boolean) {
  const m = mode.toLowerCase();
  if (m === "live") {
    return isLight
      ? "bg-emerald-50 text-emerald-800 border-emerald-300 font-semibold"
      : "bg-emerald-950/60 text-emerald-300 border-emerald-500/40 font-semibold shadow-xs";
  }
  if (m === "paper") {
    return isLight
      ? "bg-indigo-50 text-indigo-700 border-indigo-200 font-semibold"
      : "bg-indigo-950/60 text-indigo-300 border-indigo-500/40 font-semibold shadow-xs";
  }
  if (m === "simulation" || m === "backtest") {
    return isLight
      ? "bg-purple-50 text-purple-700 border-purple-200 font-semibold"
      : "bg-purple-950/60 text-purple-300 border-purple-500/40 font-semibold shadow-xs";
  }
  return isLight
    ? "bg-zinc-100 text-zinc-700 border-zinc-200 font-medium"
    : "bg-[#202025] text-zinc-300 border-[#27272a] font-medium";
}

function getStatusBadgeClasses(status: string, isLight: boolean) {
  const s = status.toLowerCase();
  if (s === "open") {
    return isLight
      ? "bg-cyan-50 text-cyan-800 border-cyan-200 font-semibold"
      : "bg-cyan-950/60 text-cyan-300 border-cyan-500/30";
  }
  if (s === "proposed") {
    return isLight
      ? "bg-amber-50 text-amber-800 border-amber-200 font-semibold"
      : "bg-amber-950/60 text-amber-300 border-amber-500/30";
  }
  return isLight
    ? "bg-zinc-100 text-zinc-600 border-zinc-200"
    : "bg-[#202025] text-zinc-400 border-[#27272a]";
}
