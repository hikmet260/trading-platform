"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import Navbar from "@/components/Navbar";
import { apiFetch, formatErrorDetail } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { StrategiesPageSkeleton } from "@/components/ui/PageSkeletons";
import { startTopLoader, stopTopLoader } from "@/components/ui/TopProgressBar";

type RuleCondition = {
  kind: "threshold" | "cross";
  indicator: "rsi" | "ema" | "sma" | "close";
  operator: "<" | "<=" | ">" | ">=" | "crosses_above" | "crosses_below";
  period?: number | null;
  comparison_indicator?: "ema" | "sma" | null;
  comparison_period?: number | null;
  value?: number | null;
};

type StrategyRule = {
  side: "buy" | "sell" | "both";
  symbol: string;
  timeframe: string;
  conditions: RuleCondition[];
  risk_pct?: number;
  stop_loss_pips?: number;
  take_profit_pips?: number;
  pip_size?: number;
  sizing_mode?: "risk_pct" | "fixed_lots";
  base_lot_size?: number | null;
  tier_lots?: number[] | null;
};

type Strategy = {
  id: string;
  name: string;
  prompt: string;
  rule: StrategyRule;
  enabled: boolean;
  broker_account_id: string | null;
  created_at: string;
};

type BrokerAccount = {
  id: string;
  name: string;
  broker: string;
  enabled: boolean;
};

const TEMPLATE_PRESETS = [
  {
    label: "RSI Oversold",
    name: "BTC RSI Oversold",
    prompt: "Buy BTC/USDT on 15m when RSI(14) < 30 risk 0.5% stop loss 200 pips take profit 400 pips",
  },
  {
    label: "EMA Golden Cross",
    name: "BTC EMA 1h Cross",
    prompt: "Buy BTC/USDT on 1h when EMA(9) crosses_above EMA(21) risk 1.0% stop loss 300 pips take profit 600 pips",
  },
  {
    label: "Tiered Scalper",
    name: "EUR/USD Tiered Grid",
    prompt: "Buy EUR/USD on 5m when RSI(14) < 25 sizing fixed base 0.05 tiers 0.05, 0.10, 0.20 stop loss 20 pips take profit 40 pips",
  },
  {
    label: "RSI Overbought",
    name: "ETH RSI Overbought",
    prompt: "Sell ETH/USDT on 15m when RSI(14) > 70 risk 0.5% stop loss 250 pips take profit 500 pips",
  },
];

export default function StrategiesPage() {
  const { toast } = useToast();
  const { confirm } = useConfirm();

  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  // Filter & Search
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "paused">("all");

  // Inline Builder State
  const [showBuilder, setShowBuilder] = useState(false);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [brokerAccountId, setBrokerAccountId] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [compiling, setCompiling] = useState(false);
  const [compiledRule, setCompiledRule] = useState<StrategyRule | null>(null);
  const [compileError, setCompileError] = useState("");
  const [saving, setSaving] = useState(false);

  // Expanded JSON state per strategy ID
  const [expandedJson, setExpandedJson] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    startTopLoader("Loading Strategies...");
    try {
      const [stratRes, accRes] = await Promise.all([
        apiFetch("/strategies", { cache: "no-store" }),
        apiFetch("/broker-accounts", { cache: "no-store" }).catch(() => null),
      ]);
      if (!stratRes.ok) throw new Error("Could not load saved strategies");
      setStrategies(await stratRes.json());

      if (accRes && accRes.ok) {
        setAccounts(await accRes.json());
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "API unavailable");
    } finally {
      setLoading(false);
      stopTopLoader();
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const accountMap = useMemo(() => {
    const map = new Map<string, BrokerAccount>();
    accounts.forEach((acc) => map.set(acc.id, acc));
    return map;
  }, [accounts]);

  async function toggle(item: Strategy) {
    const nextState = !item.enabled;
    const response = await apiFetch(`/strategies/${item.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: nextState }),
    });
    if (!response.ok) {
      const data = (await response.json().catch(() => ({}))) as { detail?: unknown };
      const err = formatErrorDetail(data.detail, "Strategy update failed");
      setError(err);
      toast.error(err, "Strategy Update Error");
      return;
    }
    toast.success(
      `Strategy "${item.name}" is now ${nextState ? "Active" : "Paused"}`,
      "Strategy Updated"
    );
    await refresh();
  }

  async function deleteStrategy(item: Strategy) {
    const ok = await confirm({
      title: "Delete Strategy?",
      description: `Are you sure you want to delete "${item.name}"? This automated pipeline will be permanently removed.`,
      confirmLabel: "Delete Strategy",
      cancelLabel: "Cancel",
      variant: "danger",
      details: [
        { label: "Strategy", value: item.name },
        { label: "Rule", value: item.prompt },
        { label: "Status", value: item.enabled ? "ACTIVE" : "PAUSED", badge: item.enabled ? "emerald" : "zinc" },
      ],
    });
    if (!ok) return;

    try {
      const res = await apiFetch(`/strategies/${item.id}`, { method: "DELETE" });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
        throw new Error(formatErrorDetail(data.detail, "Failed to delete strategy"));
      }
      toast.success(`Strategy "${item.name}" deleted`, "Strategy Removed");
      await refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed", "Error");
    }
  }

  async function testCompile() {
    if (!prompt.trim()) {
      setCompileError("Please enter a strategy prompt rule.");
      return;
    }
    setCompiling(true);
    setCompileError("");
    setCompiledRule(null);
    try {
      const res = await apiFetch("/strategies/compile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(formatErrorDetail(data.detail, "Syntax parsing error"));
      setCompiledRule(data.rule as StrategyRule);
      toast.success("Rule validated successfully", "Syntax OK");
    } catch (e) {
      setCompileError(e instanceof Error ? e.message : "Failed to compile prompt");
    } finally {
      setCompiling(false);
    }
  }

  async function saveStrategy() {
    if (!name.trim()) {
      toast.error("Please enter a strategy name", "Name Required");
      return;
    }
    if (!prompt.trim()) {
      toast.error("Please enter a natural language rule", "Prompt Required");
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch("/strategies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          prompt: prompt.trim(),
          enabled,
          broker_account_id: brokerAccountId || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(formatErrorDetail(data.detail, "Strategy creation failed"));

      toast.success(`Strategy "${name}" saved`, "Created");
      setName("");
      setPrompt("");
      setBrokerAccountId("");
      setCompiledRule(null);
      setCompileError("");
      setShowBuilder(false);
      await refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed", "Error");
    } finally {
      setSaving(false);
    }
  }

  const filteredStrategies = useMemo(() => {
    return strategies.filter((item) => {
      if (statusFilter === "active" && !item.enabled) return false;
      if (statusFilter === "paused" && item.enabled) return false;
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return (
        item.name.toLowerCase().includes(q) ||
        item.prompt.toLowerCase().includes(q) ||
        item.rule?.symbol?.toLowerCase().includes(q)
      );
    });
  }, [strategies, statusFilter, searchQuery]);

  const activeCount = strategies.filter((s) => s.enabled).length;
  const pausedCount = strategies.length - activeCount;

  if (loading) {
    return <StrategiesPageSkeleton />;
  }

  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
          <div>
            <h1 className="text-xl font-semibold text-zinc-100">Saved Strategies</h1>
            <p className="mt-0.5 text-xs text-zinc-400">
              Active and configured rule execution pipelines
            </p>
          </div>
          <div className="flex items-center gap-4 text-xs">
            <Link href="/" className="text-cyan-400 hover:text-cyan-300 transition-colors">
              ← Terminal Workspace
            </Link>
          </div>
        </header>

        {/* Action Bar & Filter Toolbar */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setShowBuilder(!showBuilder)}
              className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-3.5 py-1.5 text-xs font-semibold text-zinc-950 transition-colors cursor-pointer"
            >
              {showBuilder ? "Hide Builder" : "+ New Strategy"}
            </button>
          </div>

          <div className="flex items-center gap-2 w-full sm:w-auto">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter by name, symbol..."
              className="flex-1 sm:w-60 rounded border border-[#27272a] bg-[#141416] px-2.5 py-1.5 text-xs text-zinc-200 outline-none focus:border-cyan-500"
            />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as "all" | "active" | "paused")}
              className="rounded border border-[#27272a] bg-[#141416] px-2.5 py-1.5 text-xs text-zinc-300 outline-none focus:border-cyan-500 cursor-pointer shrink-0"
            >
              <option value="all">All ({strategies.length})</option>
              <option value="active">Active ({activeCount})</option>
              <option value="paused">Paused ({pausedCount})</option>
            </select>
          </div>
        </div>

        {/* Inline Strategy Builder */}
        {showBuilder && (
          <section className="mt-4 rounded-xl border border-[#27272a] bg-[#18181b] p-5">
            <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-[#26262b]">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                Strategy Builder
              </h2>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] text-zinc-500 mr-1">Templates:</span>
                {TEMPLATE_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => {
                      setName(p.name);
                      setPrompt(p.prompt);
                      setCompiledRule(null);
                      setCompileError("");
                    }}
                    className="rounded border border-[#27272a] bg-[#141416] hover:bg-[#222227] hover:border-zinc-600 px-2 py-0.5 text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors cursor-pointer"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                  Strategy Name
                </label>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. BTC 15m RSI Reversal"
                  className="w-full rounded border border-[#27272a] bg-[#141416] p-2 text-xs text-zinc-200 outline-none focus:border-cyan-500"
                />
              </div>

              <div>
                <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                  Execution Venue
                </label>
                <select
                  value={brokerAccountId}
                  onChange={(e) => setBrokerAccountId(e.target.value)}
                  className="w-full rounded border border-[#27272a] bg-[#141416] p-2 text-xs text-zinc-200 outline-none focus:border-zinc-500 cursor-pointer"
                >
                  <option value="">Paper Mode (Default)</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({a.broker.toUpperCase()})
                    </option>
                  ))}
                </select>
              </div>

              <div className="md:col-span-2">
                <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                  Natural Language Strategy Rule
                </label>
                <textarea
                  rows={2}
                  value={prompt}
                  onChange={(e) => {
                    setPrompt(e.target.value);
                    setCompiledRule(null);
                    setCompileError("");
                  }}
                  placeholder="e.g. Buy BTC/USDT on 15m when RSI(14) < 30 risk 0.5% stop loss 200 pips take profit 400 pips"
                  className="w-full rounded border border-[#27272a] bg-[#141416] p-2 font-mono text-xs text-zinc-200 outline-none focus:border-cyan-500"
                />
              </div>
            </div>

            {compileError && <p className="mt-2 text-xs text-amber-400">{compileError}</p>}

            {compiledRule && (
              <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-950/20 p-2.5 text-xs font-mono text-emerald-300 flex flex-wrap gap-4">
                <span>
                  Symbol: <strong>{compiledRule.symbol}</strong>
                </span>
                <span>
                  Timeframe: <strong>{compiledRule.timeframe}</strong>
                </span>
                <span>
                  Side: <strong>{compiledRule.side.toUpperCase()}</strong>
                </span>
                <span>
                  Stop Loss: <strong>{compiledRule.stop_loss_pips} pips</strong>
                </span>
                <span>
                  Take Profit: <strong>{compiledRule.take_profit_pips} pips</strong>
                </span>
                <span>
                  Risk:{" "}
                  <strong>
                    {compiledRule.sizing_mode === "fixed_lots"
                      ? `${compiledRule.base_lot_size} lots`
                      : `${compiledRule.risk_pct}%`}
                  </strong>
                </span>
              </div>
            )}

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={saving || !prompt.trim()}
                  onClick={() => void saveStrategy()}
                  className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-4 py-2 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer"
                >
                  {saving ? "Saving..." : "Save Strategy"}
                </button>
                <button
                  type="button"
                  disabled={compiling || !prompt.trim()}
                  onClick={() => void testCompile()}
                  className="rounded-lg border border-[#27272a] bg-[#141416] hover:bg-[#222227] px-3.5 py-2 text-xs text-zinc-300 transition-colors cursor-pointer"
                >
                  {compiling ? "Validating..." : "Validate Rule"}
                </button>
              </div>

              <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  className="rounded border-[#27272a] bg-[#141416]"
                />
                <span>Start Active</span>
              </label>
            </div>
          </section>
        )}

        {error && <p className="mt-3 text-xs text-amber-400">{error}</p>}

        {/* Strategy Cards */}
        <section className="mt-5 space-y-3">
          {loading && (
            <div className="rounded-xl border border-[#27272a] bg-[#18181b] p-8 text-center text-xs text-zinc-500">
              Loading strategies...
            </div>
          )}

          {!loading && filteredStrategies.length === 0 && (
            <div className="rounded-xl border border-[#27272a] bg-[#18181b] p-8 text-center text-xs text-zinc-500">
              {searchQuery || statusFilter !== "all"
                ? "No strategies matching your search or filter."
                : "No strategies saved yet. Click \"New Strategy\" above to create your first pipeline."}
            </div>
          )}

          {filteredStrategies.map((strategy) => {
            const side = (strategy.rule?.side || "buy").toLowerCase();
            const brokerAcc = strategy.broker_account_id
              ? accountMap.get(strategy.broker_account_id)
              : null;
            const isJsonOpen = expandedJson === strategy.id;

            return (
              <article
                key={strategy.id}
                className="rounded-xl border border-[#27272a] bg-[#18181b] p-4 transition-colors hover:border-[#3b3b44]"
              >
                {/* Header row */}
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-sm font-semibold text-zinc-100">{strategy.name}</h2>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-mono font-medium ${
                          side === "buy"
                            ? "bg-emerald-950/60 text-emerald-400 border border-emerald-500/30"
                            : side === "sell"
                            ? "bg-rose-950/60 text-rose-400 border border-rose-500/30"
                            : "bg-cyan-950/60 text-cyan-400 border border-cyan-500/30"
                        }`}
                      >
                        {side.toUpperCase()}
                      </span>
                      {strategy.rule?.symbol && (
                        <span className="text-[11px] font-mono font-medium text-zinc-200">
                          {strategy.rule.symbol}
                        </span>
                      )}
                      {strategy.rule?.timeframe && (
                        <span className="text-[11px] font-mono text-zinc-500">
                          · {strategy.rule.timeframe}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-[11px] text-zinc-500">
                      Saved {new Date(strategy.created_at).toLocaleString()} ·{" "}
                      {brokerAcc
                        ? `Account: ${brokerAcc.name} (${brokerAcc.broker.toUpperCase()})`
                        : strategy.broker_account_id
                        ? `Account: ${strategy.broker_account_id.slice(0, 8)}...`
                        : "Paper Mode"}
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/backtest?prompt=${encodeURIComponent(strategy.prompt)}&symbol=${encodeURIComponent(strategy.rule?.symbol || "")}&timeframe=${encodeURIComponent(strategy.rule?.timeframe || "")}`}
                      className="rounded-lg border border-[#27272a] bg-[#141416] hover:bg-[#222227] px-2.5 py-1.5 text-xs text-zinc-300 hover:text-white transition-colors"
                    >
                      Backtest
                    </Link>
                    <button
                      type="button"
                      onClick={() => void toggle(strategy)}
                      className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
                        strategy.enabled
                          ? "bg-cyan-950/40 border border-cyan-500/40 text-cyan-400"
                          : "bg-[#27272a] text-zinc-400 hover:text-zinc-200"
                      }`}
                    >
                      {strategy.enabled ? "Active" : "Paused"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void deleteStrategy(strategy)}
                      className="rounded-lg border border-[#27272a] bg-[#141416] hover:bg-red-950/40 hover:border-red-500/40 hover:text-red-400 px-2.5 py-1.5 text-xs text-zinc-400 transition-colors cursor-pointer"
                      title="Delete Strategy"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {/* Natural prompt */}
                <p className="mt-3 rounded border border-[#27272a] bg-[#141416] p-3 font-mono text-xs text-zinc-300">
                  {strategy.prompt}
                </p>

                {/* Summary Parameters Row */}
                <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <div className="flex flex-wrap items-center gap-3 text-[11px] text-zinc-400 font-mono">
                    {strategy.rule?.stop_loss_pips && (
                      <span>
                        SL: <strong className="text-zinc-200">{strategy.rule.stop_loss_pips} pips</strong>
                      </span>
                    )}
                    {strategy.rule?.take_profit_pips && (
                      <span>
                        TP: <strong className="text-zinc-200">{strategy.rule.take_profit_pips} pips</strong>
                      </span>
                    )}
                    <span>
                      Risk:{" "}
                      <strong className="text-zinc-200">
                        {strategy.rule?.sizing_mode === "fixed_lots"
                          ? `${strategy.rule.base_lot_size || 0.01} lots`
                          : `${strategy.rule?.risk_pct || 0.5}%`}
                      </strong>
                    </span>
                    {strategy.rule?.conditions?.map((c, i) => (
                      <span key={i} className="text-cyan-400/90">
                        {c.indicator?.toUpperCase()}
                        {c.period ? `(${c.period})` : ""} {c.operator}{" "}
                        {c.value ?? `${c.comparison_indicator?.toUpperCase()}(${c.comparison_period})`}
                      </span>
                    ))}
                  </div>

                  <button
                    type="button"
                    onClick={() => setExpandedJson(isJsonOpen ? null : strategy.id)}
                    className="text-[11px] text-zinc-500 hover:text-zinc-300 transition-colors cursor-pointer"
                  >
                    {isJsonOpen ? "Hide JSON" : "View JSON"}
                  </button>
                </div>

                {/* Collapsible rule JSON */}
                {isJsonOpen && (
                  <pre className="mt-2.5 overflow-x-auto rounded border border-[#27272a]/70 bg-[#141416]/90 p-3 font-mono text-[11px] text-cyan-300/90">
                    {JSON.stringify(strategy.rule, null, 2)}
                  </pre>
                )}
              </article>
            );
          })}
        </section>
      </main>
    </div>
  );
}
