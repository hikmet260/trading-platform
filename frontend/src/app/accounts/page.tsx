"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

type Account = { id: string; name: string; broker: string; enabled: boolean; configuration: Record<string, unknown>; created_at: string };

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [name, setName] = useState("");
  const [broker, setBroker] = useState("binance");
  const [credentials, setCredentials] = useState('{"apiKey":"","secret":""}');
  const [configuration, setConfiguration] = useState('{"default_type":"swap","sandbox":true,"risk_currency":"USDT"}');
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const response = await apiFetch("/broker-accounts", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load broker accounts");
    setAccounts(await response.json());
  }, []);
  useEffect(() => { void refresh().catch((error) => setMessage(error.message)); }, [refresh]);

  function chooseBroker(value: string) {
    setBroker(value);
    setCredentials(value === "mt5" ? '{"login":"","password":"","server":""}' : '{"apiKey":"","secret":"","password":""}');
    setConfiguration(value === "mt5" ? '{"terminal_path":""}' : '{"default_type":"swap","sandbox":true,"risk_currency":"USDT"}');
  }

  async function addAccount() {
    setBusy(true); setMessage("");
    try {
      const response = await apiFetch("/broker-accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, broker, credentials: JSON.parse(credentials), configuration: JSON.parse(configuration) }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Account could not be saved");
      setName(""); setMessage(`Account saved encrypted (${data.id}). Enable it after verifying the connection.`); await refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Invalid account details"); }
    finally { setBusy(false); }
  }

  async function setEnabled(account: Account) {
    setBusy(true); setMessage("");
    try {
      const response = await apiFetch(`/broker-accounts/${account.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !account.enabled }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Account state update failed");
      setMessage(account.enabled ? "Account disabled." : "Account equity verified and execution account enabled."); await refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Account state update failed"); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto min-h-screen max-w-5xl px-5 py-6 md:px-8">
    <header className="flex items-center justify-between border-b border-slate-800 pb-5"><div><p className="text-xs uppercase tracking-widest text-cyan-400">Execution configuration</p><h1 className="mt-1 text-2xl font-semibold">Broker accounts</h1></div><Link href="/" className="text-sm text-cyan-300">Market terminal</Link></header>
    <p className="mt-4 rounded-lg border border-amber-900 bg-amber-950/40 p-3 text-sm text-amber-200">Credentials are encrypted at rest. Live execution also requires the operator to enable LIVE_TRADING_ENABLED. MT5 needs a supported Windows terminal; crypto execution is limited to linear derivatives with attached stop and target support.</p>
    <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/60 p-4"><h2 className="font-semibold">Add account</h2><div className="mt-3 grid gap-3 md:grid-cols-2"><label className="text-xs text-slate-400">Account label<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm"/></label><label className="text-xs text-slate-400">Broker<select value={broker} onChange={(event) => chooseBroker(event.target.value)} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm"><option value="binance">Binance</option><option value="bybit">Bybit</option><option value="okx">OKX</option><option value="mt5">MetaTrader 5</option></select></label><label className="text-xs text-slate-400">Credentials JSON<textarea rows={4} value={credentials} onChange={(event) => setCredentials(event.target.value)} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-xs"/></label><label className="text-xs text-slate-400">Configuration JSON<textarea rows={4} value={configuration} onChange={(event) => setConfiguration(event.target.value)} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-xs"/></label></div><button disabled={busy || !name.trim()} onClick={() => void addAccount()} className="mt-3 rounded bg-cyan-800 px-4 py-2 text-sm disabled:opacity-50">Store encrypted credentials</button></section>
    {message && <p className="mt-3 text-sm text-cyan-200" role="status">{message}</p>}
    <section className="mt-5 space-y-3">{accounts.map((account) => <article key={account.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4"><div><h2 className="font-semibold">{account.name} <span className="ml-2 text-xs uppercase text-slate-400">{account.broker}</span></h2><p className="mt-1 font-mono text-xs text-slate-500">{account.id}</p><p className="mt-1 text-xs text-slate-500">{JSON.stringify(account.configuration)}</p></div><button disabled={busy} onClick={() => void setEnabled(account)} className={`rounded px-3 py-2 text-xs ${account.enabled ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-200"}`}>{account.enabled ? "Enabled · disable" : "Disabled · verify & enable"}</button></article>)}</section>
  </main>;
}
