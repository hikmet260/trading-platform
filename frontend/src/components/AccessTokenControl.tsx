"use client";

import { useEffect, useState } from "react";
import { clearSession, login, saveAccessToken } from "@/lib/api";

export default function AccessTokenControl() {
  const [mode, setMode] = useState<"user" | "key">("user");
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenant, setTenant] = useState("local");
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { setConnected(Boolean(window.localStorage.getItem("trading-platform-token"))); }, []);

  async function submit() {
    const valid = mode === "user" ? await login(email.trim(), password, tenant.trim()) : await saveAccessToken(token.trim());
    setMessage(valid ? "Signed in" : "Authentication failed");
    if (valid) { setConnected(true); setPassword(""); setToken(""); }
  }

  function logout() {
    clearSession(); setConnected(false); setMessage("Signed out");
  }

  return <div className="fixed bottom-3 right-3 z-50 flex max-w-[calc(100vw-24px)] items-center gap-2 rounded-lg border border-slate-700 bg-slate-950/95 p-2 shadow-xl">
    {connected ? <><span className="text-xs text-emerald-300">Authenticated</span><button onClick={logout} className="rounded bg-slate-800 px-2 py-1.5 text-xs">Sign out</button></> : <>
      <select aria-label="Authentication method" value={mode} onChange={(event) => setMode(event.target.value as "user" | "key")} className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs"><option value="user">Account</option><option value="key">Deployment key</option></select>
      {mode === "user" ? <><input aria-label="Tenant ID" value={tenant} onChange={(event) => setTenant(event.target.value)} placeholder="Tenant" className="w-20 rounded border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs"/><input aria-label="Email" type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Email" className="w-28 rounded border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs"/><input aria-label="Password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" className="w-28 rounded border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs"/></> : <input aria-label="Deployment access token" type="password" autoComplete="current-password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Deployment token" className="w-36 rounded border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs"/>}
      <button onClick={() => void submit()} className="rounded bg-cyan-800 px-2 py-1.5 text-xs">Sign in</button>
    </>}
    {message && <span className="text-[10px] text-slate-400" role="status">{message}</span>}
  </div>;
}
