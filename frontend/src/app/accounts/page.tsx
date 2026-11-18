"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useState } from "react";
import { Eye, EyeOff, Key, Check, Trash2, ArrowLeft } from "lucide-react";
import Navbar from "@/components/Navbar";
import { apiFetch, formatErrorDetail } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { AccountsPageSkeleton } from "@/components/ui/PageSkeletons";
import { startTopLoader, stopTopLoader } from "@/components/ui/TopProgressBar";

type Account = {
  id: string;
  name: string;
  broker: string;
  enabled: boolean;
  configuration: {
    default_type?: string;
    sandbox?: boolean;
    risk_currency?: string;
    terminal_path?: string;
    [key: string]: unknown;
  };
  created_at?: string;
};

type AgentStatus = { broker_account_id: string; online: boolean; trade_mode?: string | null; account_equity?: number | null };

const BROKER_OPTIONS = [
  { id: "binance", name: "Binance", tag: "Futures & Spot" },
  { id: "bybit", name: "Bybit", tag: "Derivatives & Spot" },
  { id: "okx", name: "OKX", tag: "Unified Margin & Swap" },
  { id: "mt5", name: "MetaTrader 5", tag: "Forex & CFDs" },
];

export default function AccountsPage() {
  const { toast } = useToast();
  const { confirm } = useConfirm();

  const [accounts, setAccounts] = useState<Account[]>([]);
  const [agentStates, setAgentStates] = useState<Record<string, AgentStatus>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  // Form State
  const [name, setName] = useState("");
  const [broker, setBroker] = useState("binance");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [isSandbox, setIsSandbox] = useState(true);
  const [contractType, setContractType] = useState<"swap" | "spot">("swap");
  const [showSecret, setShowSecret] = useState(false);
  const [agentSetup, setAgentSetup] = useState<{ token: string; name: string } | null>(null);

  const refresh = useCallback(async () => {
    startTopLoader("Loading Accounts...");
    try {
      const response = await apiFetch("/broker-accounts", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load broker accounts");
      setAccounts(await response.json());
      const agentsResponse = await apiFetch("/mt5/agents", { cache: "no-store" });
      if (agentsResponse.ok) {
        const agents: AgentStatus[] = await agentsResponse.json();
        setAgentStates(Object.fromEntries(agents.map((agent) => [agent.broker_account_id, agent])));
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Account service unavailable");
    } finally {
      setLoading(false);
      stopTopLoader();
    }
  }, [toast]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleAddAccount(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      toast.warning("Please enter an account nickname", "Missing Name");
      return;
    }

    setBusy(true);

    try {
      let credentialsPayload: Record<string, string> = {};
      let configurationPayload: Record<string, unknown> = {};

      if (broker === "mt5") {
        const response = await apiFetch("/mt5/agents", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim() }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(formatErrorDetail(data.detail, "MT5 agent could not be created"));
        setAgentSetup({ token: data.token, name: data.name });
        setName("");
        toast.success("Install the agent on the computer running MT5", "MT5 Setup Created");
        await refresh();
        return;
      } else {
        if (!apiKey.trim() || !apiSecret.trim()) {
          toast.warning("Please enter both API Key and API Secret", "Missing Credentials");
          setBusy(false);
          return;
        }
        credentialsPayload = {
          apiKey: apiKey.trim(),
          secret: apiSecret.trim(),
          password: passphrase.trim(),
        };
        configurationPayload = {
          default_type: contractType,
          sandbox: isSandbox,
          risk_currency: "USDT",
        };
      }

      const response = await apiFetch("/broker-accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          broker,
          credentials: credentialsPayload,
          configuration: configurationPayload,
        }),
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(formatErrorDetail(data.detail, "Account could not be saved"));
      }

      toast.success(`Account saved (${data.name})`, "Broker Connected");

      // Reset form
      setName("");
      setApiKey("");
      setApiSecret("");
      setPassphrase("");

      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to save account", "Save Error");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleEnabled(account: Account) {
    setBusy(true);
    try {
      const response = await apiFetch(`/broker-accounts/${account.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !account.enabled }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(formatErrorDetail(data.detail, "Account state update failed"));

      toast.success(
        account.enabled ? "Account disabled" : "Account enabled",
        "Status Updated"
      );
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Update failed", "Action Error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteAccount(account: Account) {
    const ok = await confirm({
      title: `Delete ${account.name}?`,
      description: "This will permanently remove the credentials and unlink attached strategies.",
      confirmLabel: "Delete Account",
      cancelLabel: "Cancel",
      variant: "danger",
    });
    if (!ok) return;

    setBusy(true);
    try {
      const res = await apiFetch(`/broker-accounts/${account.id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Could not delete account");
      toast.info("Account removed", "Deleted");
      await refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to delete account", "Error");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <AccountsPageSkeleton />;
  }

  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />

      {/* Full-width container across the screen */}
      <main className="w-full px-4 py-6 md:px-8">
        {/* Header without badges */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-4">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-zinc-100">
              Broker Accounts
            </h1>
            <p className="mt-1 text-xs text-zinc-400">
              Connect external exchanges and brokers to execute automated strategies.
            </p>
          </div>

          <Link
            href="/"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-[#27272a] bg-[#18181b] text-zinc-300 hover:text-cyan-400 hover:border-cyan-500/40 transition-colors self-start sm:self-auto"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Terminal Workspace</span>
          </Link>
        </div>

        {/* 1. Connect Venue Card (Full-width clean surface) */}
        <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 sm:p-6 transition-all mb-8">
          <div className="flex items-center gap-2 mb-5">
            <Key className="w-4 h-4 text-cyan-400" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
              Connect Broker Venue
            </h2>
          </div>

          <form onSubmit={handleAddAccount} className="space-y-5">
            {/* Account Label */}
            <div>
              <label className="block text-xs font-medium mb-1.5 text-zinc-300">
                Account Nickname
              </label>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Binance Spot Alpha"
                className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs text-zinc-100 placeholder-zinc-500 transition-colors outline-none font-sans focus:border-cyan-500"
              />
            </div>

            {/* Venue Selector */}
            <div>
              <label className="block text-xs font-medium mb-1.5 text-zinc-300">
                Broker Venue
              </label>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                {BROKER_OPTIONS.map((opt) => {
                  const isSelected = broker === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => setBroker(opt.id)}
                      className={`flex flex-col items-start p-3 rounded-lg border text-left transition-all cursor-pointer ${
                        isSelected
                          ? "bg-cyan-950/40 border-cyan-500 text-cyan-200"
                          : "bg-[#141416] border-[#27272a] text-zinc-300 hover:border-zinc-600 hover:bg-[#1a1a1e]"
                      }`}
                    >
                      <div className="flex items-center justify-between w-full mb-0.5">
                        <span className="font-semibold text-xs">{opt.name}</span>
                        {isSelected && <Check className="w-3.5 h-3.5 text-cyan-400 shrink-0" />}
                      </div>
                      <span
                        className={`text-[10px] ${
                          isSelected ? "text-cyan-300" : "text-zinc-400"
                        }`}
                      >
                        {opt.tag}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Credentials Fields */}
            {broker === "mt5" ? (
              <div className="rounded-lg border border-cyan-900/60 bg-cyan-950/20 p-4 text-xs text-zinc-300">
                <p className="font-semibold text-cyan-200">Windows terminal agent</p>
                <p className="mt-1">Sign in to MT5 on the Windows computer, then run the outbound agent there. Broker credentials stay on that computer.</p>
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="block text-xs font-medium mb-1 text-zinc-300">
                    API Key
                  </label>
                  <input
                    type="text"
                    required
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="Enter public API key"
                    className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs font-mono text-zinc-100 placeholder-zinc-500 outline-none focus:border-cyan-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1 text-zinc-300">
                    API Secret
                  </label>
                  <div className="relative">
                    <input
                      type={showSecret ? "text" : "password"}
                      required
                      value={apiSecret}
                      onChange={(e) => setApiSecret(e.target.value)}
                      placeholder="Enter private API secret"
                      className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs font-mono pr-9 text-zinc-100 placeholder-zinc-500 outline-none focus:border-cyan-500"
                    />
                    <button
                      type="button"
                      onClick={() => setShowSecret(!showSecret)}
                      className="absolute right-2.5 top-2.5 text-zinc-400 hover:text-zinc-200 cursor-pointer"
                    >
                      {showSecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>

                {broker === "okx" && (
                  <div className="sm:col-span-2">
                    <label className="block text-xs font-medium mb-1 text-zinc-300">
                      Passphrase
                    </label>
                    <input
                      type="password"
                      required
                      value={passphrase}
                      onChange={(e) => setPassphrase(e.target.value)}
                      placeholder="OKX API Passphrase"
                      className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs font-mono text-zinc-100 placeholder-zinc-500 outline-none focus:border-cyan-500"
                    />
                  </div>
                )}
              </div>
            )}

            {/* Environment & Contract Type */}
            <div className="grid gap-3 sm:grid-cols-2 pt-2">
              {/* Sandbox vs Live */}
              {broker !== "mt5" && <div>
                <label className="block text-xs font-medium mb-1.5 text-zinc-300">
                  Environment
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setIsSandbox(true)}
                    className={`flex-1 py-1.5 px-3 rounded-lg border text-xs font-medium transition-all cursor-pointer ${
                      isSandbox
                        ? "bg-cyan-950/40 border-cyan-500 text-cyan-300"
                        : "bg-[#141416] text-zinc-400 border-[#27272a] hover:border-zinc-600"
                    }`}
                  >
                    Demo / Sandbox Testnet
                  </button>
                  <button
                    type="button"
                    onClick={() => setIsSandbox(false)}
                    className={`flex-1 py-1.5 px-3 rounded-lg border text-xs font-medium transition-all cursor-pointer ${
                      !isSandbox
                        ? "bg-cyan-950/40 border-cyan-500 text-cyan-300"
                        : "bg-[#141416] text-zinc-400 border-[#27272a] hover:border-zinc-600"
                    }`}
                  >
                    Live Exchange (Real Funds)
                  </button>
                </div>
              </div>}

              {/* Market Type (Crypto only) */}
              {broker !== "mt5" && (
                <div>
                  <label className="block text-xs font-medium mb-1.5 text-zinc-300">
                    Contract Type
                  </label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setContractType("swap")}
                      className={`flex-1 py-1.5 px-3 rounded-lg border text-xs font-medium transition-all cursor-pointer ${
                        contractType === "swap"
                          ? "bg-cyan-950/40 border-cyan-500 text-cyan-300"
                          : "bg-[#141416] text-zinc-400 border-[#27272a] hover:border-zinc-600"
                      }`}
                    >
                      Perpetual Futures (Swap)
                    </button>
                    <button
                      type="button"
                      onClick={() => setContractType("spot")}
                      className={`flex-1 py-1.5 px-3 rounded-lg border text-xs font-medium transition-all cursor-pointer ${
                        contractType === "spot"
                          ? "bg-cyan-950/40 border-cyan-500 text-cyan-300"
                          : "bg-[#141416] text-zinc-400 border-[#27272a] hover:border-zinc-600"
                      }`}
                    >
                      Spot Market
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Primary Action Button */}
            <div className="flex items-center justify-end pt-1">
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-5 py-2 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer shadow-sm shadow-cyan-500/20"
              >
                {busy ? "Connecting..." : "Connect Broker Account"}
              </button>
            </div>
          </form>
        </section>

        {agentSetup && (
          <section className="mb-8 rounded-xl border border-cyan-800/60 bg-cyan-950/20 p-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-sm font-semibold text-cyan-100">Finish MT5 agent setup: {agentSetup.name}</h2>
                <p className="mt-1 text-xs text-zinc-300">Copy this token now. It is shown only once. Keep it private and configure it on the Windows computer running MT5.</p>
              </div>
              <button type="button" onClick={() => setAgentSetup(null)} className="text-xs text-zinc-400 hover:text-white">Dismiss</button>
            </div>
            <div className="mt-4 flex flex-col gap-2 sm:flex-row">
              <input readOnly value={agentSetup.token} className="min-w-0 flex-1 rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 font-mono text-xs text-zinc-100" />
              <button type="button" onClick={() => void navigator.clipboard.writeText(agentSetup.token)} className="rounded-lg bg-cyan-500 px-4 py-2 text-xs font-semibold text-zinc-950">Copy token</button>
            </div>
            <ol className="mt-4 list-decimal space-y-1 pl-5 text-xs text-zinc-300">
              <li>Install Python 3.11+ and the MetaTrader 5 terminal on the Windows computer.</li>
              <li>Open MT5, sign in to the demo account, and enable algorithmic trading.</li>
              <li>Copy <code>backend/mt5_agent</code> from this project to Windows and install <code>backend/requirements-mt5-windows.txt</code>.</li>
              <li>Set <code>TRADING_PLATFORM_URL</code> to this deployed site and <code>TRADING_PLATFORM_AGENT_TOKEN</code> to the token above.</li>
              <li>Run <code>py backend/mt5_agent/agent.py</code> as the same Windows user as MT5. Configure Task Scheduler to start it at user logon.</li>
            </ol>
            <p className="mt-3 text-xs text-amber-200">The agent only submits demo orders by default. Live orders remain blocked by platform safety settings.</p>
          </section>
        )}

        {/* 2. Connected Accounts List */}
        <section className="space-y-3">
          <div className="flex items-center justify-between pb-1">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
              Connected Accounts ({accounts.length})
            </h2>
            <span className="text-xs text-zinc-500">
              Order execution venues
            </span>
          </div>

          {accounts.length === 0 && (
            <div className="rounded-xl border border-[#27272a] bg-[#18181b] p-8 text-center text-xs text-zinc-400">
              No broker accounts connected yet. Connect an exchange above.
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {accounts.map((account) => {
              const cfg = account.configuration || {};
              const isSandboxMode = cfg.sandbox !== false;
              const type = cfg.default_type || (account.broker === "mt5" ? "Forex" : "Swap");

              return (
                <article
                  key={account.id}
                  className="flex flex-col justify-between p-4 rounded-xl border border-[#27272a] bg-[#18181b] transition-all"
                >
                  <div>
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <div>
                        <h3 className="text-xs font-bold leading-tight uppercase font-mono text-zinc-100">
                          {account.name}
                        </h3>
                        <p className="text-[11px] uppercase font-mono mt-0.5 text-zinc-400">
                          {account.broker === "mt5"
                            ? "MT5 · Windows Agent"
                            : `${account.broker} · ${String(type).toUpperCase()} · ${isSandboxMode ? "Demo Sandbox" : "Live"}`}
                        </p>
                      </div>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void handleDeleteAccount(account)}
                        title="Delete Account"
                        className="p-1 rounded text-zinc-500 hover:text-rose-400 hover:bg-rose-950/40 transition-colors cursor-pointer"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    <p className="text-[10px] font-mono break-all mb-3 text-zinc-500">
                      ID: {account.id}
                    </p>
                    {account.broker === "mt5" && (
                      <p className={`mb-2 text-[11px] ${agentStates[account.id]?.online ? "text-emerald-300" : "text-amber-300"}`}>
                        Agent: {agentStates[account.id]?.online ? `Online · ${agentStates[account.id]?.trade_mode ?? "account unverified"}` : "Offline"}
                      </p>
                    )}
                  </div>

                  {/* Status Toggle Button */}
                  <div className="pt-3 flex items-center justify-between">
                    <span
                      className={`text-[11px] font-medium ${
                        account.enabled ? "text-emerald-400" : "text-zinc-500"
                      }`}
                    >
                      {account.enabled ? "Active" : "Disabled"}
                    </span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleToggleEnabled(account)}
                      className={`px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer border ${
                        account.enabled
                          ? "bg-emerald-950/40 border-emerald-500/40 text-emerald-300"
                          : "bg-[#141416] border-[#27272a] text-zinc-400 hover:text-zinc-200"
                      }`}
                    >
                      {account.enabled ? "Disable" : "Enable"}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </main>
    </div>
  );
}
