"use client";

import { useEffect, useState } from "react";
import { clearSession, login, saveAccessToken } from "@/lib/api";

export default function AccessTokenControl({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"user" | "key">("user");
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenant, setTenant] = useState("local");
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setConnected(Boolean(window.localStorage.getItem("trading-platform-token")));
    }
  }, [isOpen]);

  if (!isOpen) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setMessage("");
    try {
      let valid = false;
      if (mode === "user") {
        const res = await login(email.trim(), password, tenant.trim());
        valid = res.success;
        if (!valid && res.error) setMessage(res.error);
      } else {
        valid = await saveAccessToken(token.trim());
      }
      if (valid) {
        setMessage("Authenticated successfully");
        setConnected(true);
        setPassword("");
        setToken("");
        setTimeout(() => onClose(), 800);
      } else if (!message) {
        setMessage("Authentication failed. Check credentials.");
      }
    } catch {
      setMessage("Connection error. Could not authenticate.");
    } finally {
      setLoading(false);
    }
  }

  function handleLogout() {
    clearSession();
    setConnected(false);
    setMessage("Signed out");
    setTimeout(() => onClose(), 600);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl border border-[#27272a] bg-[#18181b] p-6">
        <div className="flex items-center justify-between border-b border-[#27272a] pb-4">
          <div>
            <h3 className="text-base font-medium text-zinc-100">
              {connected ? "Session & Authentication" : "Sign In to Terminal"}
            </h3>
            <p className="mt-0.5 text-xs text-zinc-400">
              {connected ? "Active operator session" : "Access protected trading endpoints"}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-[#27272a] hover:text-zinc-200 transition-colors"
            aria-label="Close"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {connected ? (
          <div className="mt-5 space-y-4">
            <div className="flex items-center gap-2.5 text-xs text-emerald-400">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span>Operator session active</span>
            </div>
            <p className="text-xs text-zinc-400">
              Your API session token is loaded in local storage and attached to requests.
            </p>
            <div className="pt-2 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-xs text-zinc-300 hover:bg-[#27272a] transition-colors"
              >
                Close
              </button>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-lg bg-red-950/40 border border-red-900/40 px-4 py-2 text-xs font-medium text-red-300 hover:bg-red-900/60 transition-colors"
              >
                Sign Out
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-5 space-y-4">
            <div className="flex rounded-lg bg-[#141416] p-1 border border-[#27272a]">
              <button
                type="button"
                onClick={() => setMode("user")}
                className={`flex-1 rounded-md py-1.5 text-xs font-medium transition-colors ${
                  mode === "user" ? "bg-[#27272a] text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                Operator Account
              </button>
              <button
                type="button"
                onClick={() => setMode("key")}
                className={`flex-1 rounded-md py-1.5 text-xs font-medium transition-colors ${
                  mode === "key" ? "bg-[#27272a] text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                Deployment Key
              </button>
            </div>

            {mode === "user" ? (
              <div className="space-y-3">
                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">Tenant ID</label>
                  <input
                    value={tenant}
                    onChange={(e) => setTenant(e.target.value)}
                    placeholder="local"
                    className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-cyan-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">Email</label>
                  <input
                    type="email"
                    autoComplete="username"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="operator@platform.local"
                    className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-cyan-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">Password</label>
                  <input
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-cyan-500 focus:outline-none"
                  />
                </div>
              </div>
            ) : (
              <div>
                <label className="block text-[11px] font-medium text-zinc-400 mb-1">Access Token</label>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Paste static token..."
                  className="w-full rounded-lg border border-[#27272a] bg-[#141416] px-3 py-2 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-cyan-500 focus:outline-none"
                />
              </div>
            )}

            {message && (
              <p
                className={`text-xs ${
                  message.includes("success") ? "text-emerald-400" : "text-amber-400"
                }`}
              >
                {message}
              </p>
            )}

            <div className="pt-2 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-xs text-zinc-400 hover:bg-[#27272a] hover:text-zinc-200 transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading}
                className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-4 py-2 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer"
              >
                {loading ? "Verifying..." : "Sign In"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
