"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { useTheme } from "@/context/ThemeContext";
import { apiFetch, formatErrorDetail } from "@/lib/api";
import PushNotifications from "@/components/PushNotifications";
import { SettingsPageSkeleton } from "@/components/ui/PageSkeletons";
import { startTopLoader, stopTopLoader } from "@/components/ui/TopProgressBar";

type Tab = "account" | "ai" | "telegram" | "notifications" | "risk";

type SystemSettings = {
  tenant_id: string;
  ai: {
    provider: string;
    model: string;
    configured: boolean;
    masked_key: string;
    temperature: number;
    system_prompt: string;
  };
  telegram: {
    enabled: boolean;
    configured: boolean;
    masked_token: string;
    chat_id: string;
    allowed_user_ids: string;
    timezone: string;
  };
  risk: {
    execution_mode: "semi_auto" | "full_auto";
    max_daily_drawdown_pct: number;
    news_blackout_minutes: number;
    paper_account_equity: number;
    account_currency: string;
    max_trade_risk_pct: number;
    max_open_risk_pct: number;
    max_notional_multiple: number;
    max_positions_per_tenant: number;
    max_daily_trades: number;
    proposal_ttl_seconds: number;
  };
  updated_at: string | null;
};

export default function SettingsPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const { theme, setTheme, chartEngine, setChartEngine } = useTheme();

  const [activeTab, setActiveTab] = useState<Tab>("account");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // System Settings state
  const [settings, setSettings] = useState<SystemSettings | null>(null);

  // Form states - AI
  const [aiProvider, setAiProvider] = useState("openai");
  const [aiModel, setAiModel] = useState("gpt-4o");
  const [aiApiKey, setAiApiKey] = useState("");
  const [showAiKey, setShowAiKey] = useState(false);
  const [aiTemperature, setAiTemperature] = useState(0.2);
  const [aiSystemPrompt, setAiSystemPrompt] = useState("");
  const [testingAi, setTestingAi] = useState(false);
  const [aiTestResult, setAiTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Form states - Telegram
  const [tgEnabled, setTgEnabled] = useState(false);
  const [tgToken, setTgToken] = useState("");
  const [showTgToken, setShowTgToken] = useState(false);
  const [tgChatId, setTgChatId] = useState("");
  const [tgAllowedUsers, setTgAllowedUsers] = useState("");
  const [tgTimezone, setTgTimezone] = useState("UTC");
  const [testingTg, setTestingTg] = useState(false);
  const [tgTestResult, setTgTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Form states - Risk
  const [executionMode, setExecutionMode] = useState<"semi_auto" | "full_auto">("semi_auto");
  const [maxDrawdown, setMaxDrawdown] = useState(3.0);
  const [blackoutMinutes, setBlackoutMinutes] = useState(15);
  const [paperEquity, setPaperEquity] = useState(10000);
  const [accountCurrency, setAccountCurrency] = useState("USD");
  const [maxTradeRisk, setMaxTradeRisk] = useState(1.0);
  const [maxOpenRisk, setMaxOpenRisk] = useState(2.0);
  const [maxNotionalMultiple, setMaxNotionalMultiple] = useState(3.0);
  const [maxPositions, setMaxPositions] = useState(5);
  const [maxDailyTrades, setMaxDailyTrades] = useState(20);
  const [proposalTtl, setProposalTtl] = useState(900);

  // Form states - Account Password
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  const fetchSettings = useCallback(async () => {
    try {
      setLoading(true);
      startTopLoader("Loading Settings...");
      const res = await apiFetch("/system/settings");
      if (!res.ok) throw new Error("Failed to load settings");
      const data: SystemSettings = await res.json();
      setSettings(data);

      // Populate AI
      setAiProvider(data.ai.provider || "openai");
      setAiModel(data.ai.model || "gpt-4o");
      setAiApiKey(data.ai.masked_key || "");
      setAiTemperature(data.ai.temperature ?? 0.2);
      setAiSystemPrompt(data.ai.system_prompt || "");

      // Populate Telegram
      setTgEnabled(data.telegram.enabled ?? false);
      setTgToken(data.telegram.masked_token || "");
      setTgChatId(data.telegram.chat_id || "");
      setTgAllowedUsers(data.telegram.allowed_user_ids || "");
      setTgTimezone(data.telegram.timezone || "UTC");

      // Populate Risk
      setExecutionMode(data.risk.execution_mode || "semi_auto");
      setMaxDrawdown(data.risk.max_daily_drawdown_pct ?? 3.0);
      setBlackoutMinutes(data.risk.news_blackout_minutes ?? 15);
      setPaperEquity(data.risk.paper_account_equity ?? 10000);
      setAccountCurrency(data.risk.account_currency || "USD");
      setMaxTradeRisk(data.risk.max_trade_risk_pct ?? 1.0);
      setMaxOpenRisk(data.risk.max_open_risk_pct ?? 2.0);
      setMaxNotionalMultiple(data.risk.max_notional_multiple ?? 3.0);
      setMaxPositions(data.risk.max_positions_per_tenant ?? 5);
      setMaxDailyTrades(data.risk.max_daily_trades ?? 20);
      setProposalTtl(data.risk.proposal_ttl_seconds ?? 900);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Error connecting to configuration server",
        "Loading Settings Failed"
      );
    } finally {
      setLoading(false);
      stopTopLoader();
    }
  }, [toast]);

  useEffect(() => {
    void fetchSettings();
  }, [fetchSettings]);

  // Handle saving System/AI/Telegram/Risk settings
  const handleSaveSettings = async () => {
    setSaving(true);
    startTopLoader("Saving Settings...");
    setAiTestResult(null);
    setTgTestResult(null);
    try {
      const payload = {
        ai_provider: aiProvider,
        ai_model: aiModel,
        ai_api_key: aiApiKey,
        ai_temperature: Number(aiTemperature),
        ai_system_prompt: aiSystemPrompt,

        telegram_enabled: tgEnabled,
        telegram_bot_token: tgToken,
        telegram_chat_id: tgChatId,
        telegram_allowed_user_ids: tgAllowedUsers,
        telegram_timezone: tgTimezone,

        execution_mode: executionMode,
        max_daily_drawdown_pct: Number(maxDrawdown),
        news_blackout_minutes: Number(blackoutMinutes),
        paper_account_equity: Number(paperEquity),
        account_currency: accountCurrency,
        max_trade_risk_pct: Number(maxTradeRisk),
        max_open_risk_pct: Number(maxOpenRisk),
        max_notional_multiple: Number(maxNotionalMultiple),
        max_positions_per_tenant: Number(maxPositions),
        max_daily_trades: Number(maxDailyTrades),
        proposal_ttl_seconds: Number(proposalTtl),
      };

      const res = await apiFetch("/system/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(formatErrorDetail(data.detail, "Failed to update settings"));
      }

      setSettings(data);
      setAiApiKey(data.ai.masked_key || "");
      setTgToken(data.telegram.masked_token || "");
      toast.success("All configuration changes saved securely.", "Settings Saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Unknown error occurred", "Save Failed");
    } finally {
      setSaving(false);
      stopTopLoader();
    }
  };

  // Handle testing AI
  const handleTestAi = async () => {
    setTestingAi(true);
    setAiTestResult(null);
    try {
      const res = await apiFetch("/system/settings/test-ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: aiProvider,
          api_key: aiApiKey,
          model: aiModel,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        const errorMsg = data.error || formatErrorDetail(data.detail, "AI test failed");
        setAiTestResult({ success: false, message: errorMsg });
        toast.error(errorMsg, "AI Connection Failed");
      } else {
        setAiTestResult({ success: true, message: data.message });
        toast.success(data.message, "AI Verified");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Connection failed";
      setAiTestResult({ success: false, message: msg });
      toast.error(msg, "AI Verification Failed");
    } finally {
      setTestingAi(false);
    }
  };

  // Handle testing Telegram
  const handleTestTg = async () => {
    setTestingTg(true);
    setTgTestResult(null);
    try {
      const res = await apiFetch("/system/settings/test-telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bot_token: tgToken,
          chat_id: tgChatId,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        const errorMsg = data.error || formatErrorDetail(data.detail, "Telegram test failed");
        setTgTestResult({ success: false, message: errorMsg });
        toast.error(errorMsg, "Telegram Failed");
      } else {
        setTgTestResult({ success: true, message: data.message });
        toast.success(data.message, "Telegram Alert Sent");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Network error";
      setTgTestResult({ success: false, message: msg });
      toast.error(msg, "Telegram Test Failed");
    } finally {
      setTestingTg(false);
    }
  };

  // Handle Password Change
  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 12) {
      toast.warning("New password must contain at least 12 characters", "Validation Error");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.warning("New password and confirmation do not match", "Mismatch Error");
      return;
    }

    setChangingPassword(true);
    try {
      const res = await apiFetch("/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(formatErrorDetail(data.detail, "Password update failed"));
      }

      toast.success("Your password has been changed successfully.", "Password Updated");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Password change failed", "Error");
    } finally {
      setChangingPassword(false);
    }
  };

  const modelOptions: Record<string, string[]> = {
    gemini: [
      "gemini-3.8-flash",
      "gemini-3.7-flash",
      "gemini-3.6-flash",
      "gemini-3.5-flash-lite",
      "gemini-3.5-flash",
      "gemini-3.1-flash-lite",
    ],
    anthropic: [
      "claude-sonnet-4-6",
      "claude-opus-4-8",
      "claude-haiku-4-5-20251001",
    ],
    openai: [
      "gpt-4.5-preview",
      "gpt-4o",
      "gpt-4o-mini",
      "o3-mini",
      "o1",
      "o1-mini",
      "o1-preview",
      "gpt-4-turbo",
    ],
    ollama: [
      "deepseek-r1:8b",
      "deepseek-r1:14b",
      "deepseek-r1:32b",
      "llama3.3",
      "qwen2.5-coder:7b",
      "qwen2.5-coder:14b",
      "mistral",
    ],
  };

  if (loading) {
    return <SettingsPageSkeleton />;
  }

  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] font-sans">
      <Navbar />

      <main className="w-full px-4 py-6 md:px-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262b] pb-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-xl font-semibold text-zinc-100">Client Settings & Platform Config</h1>
              <span className="text-[10px] bg-cyan-950/60 text-cyan-300 border border-cyan-500/30 px-2 py-0.5 rounded font-mono font-medium">
                Tenant: {user?.tenant_id || "local"}
              </span>
            </div>
            <p className="mt-1 text-xs text-zinc-400">
              Manage your personal account credentials, AI integrations, Telegram bot alerts, and execution risk parameters.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors flex items-center gap-1.5"
            >
              <span>← Terminal</span>
            </Link>
            <button
              type="button"
              onClick={handleSaveSettings}
              disabled={saving || loading}
              className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-4 py-2 text-xs font-semibold text-zinc-950 transition-all shadow-md shadow-cyan-950/40 disabled:opacity-50 flex items-center gap-1.5 cursor-pointer"
            >
              {saving ? (
                <>
                  <div className="w-3.5 h-3.5 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" />
                  <span>Saving Changes...</span>
                </>
              ) : (
                <>
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span>Save All Settings</span>
                </>
              )}
            </button>
          </div>
        </header>

        {/* Tab Navigation (Horizontally Swipeable on Mobile, Full Row on Desktop) */}
        <div className="mt-6 flex items-center gap-2 border-b border-[#26262b] pb-2 overflow-x-auto no-scrollbar whitespace-nowrap">
          <button
            type="button"
            onClick={() => setActiveTab("account")}
            className={`flex items-center justify-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all cursor-pointer shrink-0 ${
              activeTab === "account"
                ? "border-cyan-500 text-cyan-400 bg-cyan-500/10 rounded-t-lg font-semibold"
                : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 hover:bg-[#18181b]/50 rounded-t-lg"
            }`}
          >
            <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
            <span>Account & Security</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("ai")}
            className={`flex items-center justify-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all cursor-pointer shrink-0 ${
              activeTab === "ai"
                ? "border-cyan-500 text-cyan-400 bg-cyan-500/10 rounded-t-lg font-semibold"
                : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 hover:bg-[#18181b]/50 rounded-t-lg"
            }`}
          >
            <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            <span>AI Model & API</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("telegram")}
            className={`flex items-center justify-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all cursor-pointer shrink-0 ${
              activeTab === "telegram"
                ? "border-cyan-500 text-cyan-400 bg-cyan-500/10 rounded-t-lg font-semibold"
                : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 hover:bg-[#18181b]/50 rounded-t-lg"
            }`}
          >
            <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
            <span>Telegram Bot Alerts</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("risk")}
            className={`flex items-center justify-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all cursor-pointer shrink-0 ${
              activeTab === "risk"
                ? "border-cyan-500 text-cyan-400 bg-cyan-500/10 rounded-t-lg font-semibold"
                : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 hover:bg-[#18181b]/50 rounded-t-lg"
            }`}
          >
            <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            <span>Engine & Risk Limits</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("notifications")}
            className={`flex items-center justify-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all cursor-pointer shrink-0 ${
              activeTab === "notifications"
                ? "border-cyan-500 text-cyan-400 bg-cyan-500/10 rounded-t-lg font-semibold"
                : "border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 hover:bg-[#18181b]/50 rounded-t-lg"
            }`}
          >
            <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <span>Browser Notifications</span>
          </button>
        </div>

        {/* Tab 1: Account & Security */}
        {activeTab === "account" && (
          <div className="mt-6 grid gap-6 md:grid-cols-2">
            {/* Profile Overview */}
            <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 shadow-sm">
              <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                  User Identity Profile
                </h2>
                <span className="text-[10px] font-mono text-zinc-500 uppercase">
                  Session Verified
                </span>
              </div>

              <div className="mt-4 space-y-4">
                <div>
                  <label className="block text-[11px] font-medium text-zinc-400">Email Address</label>
                  <div className="mt-1 flex items-center justify-between rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs font-mono text-zinc-200">
                    <span>{user?.email || "Operator"}</span>
                    {user?.is_platform_admin && (
                      <span className="rounded bg-cyan-900/40 border border-cyan-500/30 px-1.5 py-0.5 text-[10px] font-medium text-cyan-300">
                        Platform Admin
                      </span>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[11px] font-medium text-zinc-400">Tenant Domain</label>
                    <div className="mt-1 rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs font-mono text-zinc-300">
                      {user?.tenant_id || "local"}
                    </div>
                  </div>
                  <div>
                    <label className="block text-[11px] font-medium text-zinc-400">Internal User ID</label>
                    <div className="mt-1 rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs font-mono text-zinc-400 truncate" title={user?.id || ""}>
                      {user?.id ? `${user.id.slice(0, 12)}...` : "N/A"}
                    </div>
                  </div>
                </div>

                <div className="pt-2">
                  <div className="rounded-lg border border-[#27272a] bg-[#141416] p-3 text-xs text-zinc-400 space-y-1.5">
                    <p className="font-medium text-zinc-300">Security Notice</p>
                    <p className="text-[11px] leading-relaxed text-zinc-400">
                      Broker account keys and sensitive secrets are encrypted with Fernet symmetric cryptography before touching disk or database storage.
                    </p>
                  </div>
                </div>
              </div>
            </section>

            {/* Change Password Form */}
            <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 shadow-sm">
              <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                  Update Account Password
                </h2>
                <button
                  type="button"
                  onClick={() => setShowPasswords(!showPasswords)}
                  className="text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors flex items-center gap-1 cursor-pointer"
                >
                  <span>{showPasswords ? "Hide" : "Show"}</span>
                </button>
              </div>

              <form onSubmit={handleChangePassword} className="mt-4 space-y-4">
                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Current Password
                  </label>
                  <input
                    type={showPasswords ? "text" : "password"}
                    required
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    placeholder="Enter current password"
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    New Password (min 12 characters)
                  </label>
                  <input
                    type={showPasswords ? "text" : "password"}
                    required
                    minLength={12}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="Enter new strong password"
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Confirm New Password
                  </label>
                  <input
                    type={showPasswords ? "text" : "password"}
                    required
                    minLength={12}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Confirm new password"
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500 font-mono"
                  />
                </div>

                <button
                  type="submit"
                  disabled={changingPassword || !currentPassword || !newPassword}
                  className="w-full rounded-lg bg-[#27272f] hover:bg-[#32323c] border border-[#373742] py-2 text-xs font-semibold text-zinc-200 transition-colors disabled:opacity-50 flex items-center justify-center gap-2 cursor-pointer"
                >
                  {changingPassword ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-zinc-200 border-t-transparent rounded-full animate-spin" />
                      <span>Updating Password...</span>
                    </>
                  ) : (
                    <span>Change Password</span>
                  )}
                </button>
              </form>
            </section>

            {/* Terminal Display & Chart Engine Preferences */}
            <section className="md:col-span-2 rounded-xl border border-[#27272a] bg-[#18181b] p-5 shadow-sm">
              <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
                <div>
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                    Terminal Display & Chart Preferences
                  </h2>
                  <p className="mt-0.5 text-[11px] text-zinc-400">
                    Customize your trading terminal interface theme and select your preferred candlestick chart engine.
                  </p>
                </div>
                <span className="text-[10px] font-mono text-zinc-500 uppercase">
                  Client Preference
                </span>
              </div>

              <div className="mt-5 grid gap-6 md:grid-cols-2">
                {/* Theme Selector */}
                <div>
                  <label className="block text-[11px] font-semibold text-zinc-300 mb-2">
                    Workspace Color Theme
                  </label>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <button
                      type="button"
                      onClick={() => {
                        setTheme("dark");
                        toast.info("Theme set to Dark Mode", "Display Updated");
                      }}
                      className={`relative flex flex-col items-start rounded-lg border p-3.5 text-left transition-all cursor-pointer ${
                        theme === "dark"
                          ? "border-cyan-500 bg-cyan-500/10 ring-1 ring-cyan-500/30"
                          : "border-[#2b2b32] bg-[#131316] hover:border-zinc-700"
                      }`}
                    >
                      <div className="flex items-center justify-between w-full">
                        <div className="flex items-center gap-2">
                          <span className="w-3 h-3 rounded-full bg-[#18181b] border border-zinc-600" />
                          <span className="text-xs font-medium text-zinc-200">Dark Theme</span>
                        </div>
                        {theme === "dark" && (
                          <span className="text-[10px] bg-cyan-500/20 text-cyan-400 px-1.5 py-0.5 rounded font-mono font-medium">
                            Active
                          </span>
                        )}
                      </div>
                      <p className="mt-2 text-[11px] text-zinc-400 leading-relaxed">
                        Deep dark terminal palette designed to minimize eye fatigue during extended trading sessions.
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setTheme("light");
                        toast.info("Theme set to Light Mode", "Display Updated");
                      }}
                      className={`relative flex flex-col items-start rounded-lg border p-3.5 text-left transition-all cursor-pointer ${
                        theme === "light"
                          ? "border-cyan-500 bg-cyan-500/10 ring-1 ring-cyan-500/30"
                          : "border-[#2b2b32] bg-[#131316] hover:border-zinc-700"
                      }`}
                    >
                      <div className="flex items-center justify-between w-full">
                        <div className="flex items-center gap-2">
                          <span className="w-3 h-3 rounded-full bg-white border border-zinc-400" />
                          <span className="text-xs font-medium text-zinc-200">Light Theme</span>
                        </div>
                        {theme === "light" && (
                          <span className="text-[10px] bg-cyan-500/20 text-cyan-400 px-1.5 py-0.5 rounded font-mono font-medium">
                            Active
                          </span>
                        )}
                      </div>
                      <p className="mt-2 text-[11px] text-zinc-400 leading-relaxed">
                        Crisp, high-contrast light surface designed for bright environments and clean documentation.
                      </p>
                    </button>
                  </div>
                </div>

                {/* Chart Engine Selector */}
                <div>
                  <label className="block text-[11px] font-semibold text-zinc-300 mb-2">
                    Candlestick Chart Engine
                  </label>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <button
                      type="button"
                      onClick={() => {
                        setChartEngine("ink");
                        toast.info("Chart Engine set to Ink Minimal (SVG)", "Chart Updated");
                      }}
                      className={`relative flex flex-col items-start rounded-lg border p-3.5 text-left transition-all cursor-pointer ${
                        chartEngine === "ink"
                          ? "border-cyan-500 bg-cyan-500/10 ring-1 ring-cyan-500/30"
                          : "border-[#2b2b32] bg-[#131316] hover:border-zinc-700"
                      }`}
                    >
                      <div className="flex items-center justify-between w-full">
                        <div className="flex items-center gap-1.5">
                          <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                          </svg>
                          <span className="text-xs font-medium text-zinc-200">Ink Minimal (SVG)</span>
                        </div>
                        {chartEngine === "ink" && (
                          <span className="text-[10px] bg-cyan-500/20 text-cyan-400 px-1.5 py-0.5 rounded font-mono font-medium">
                            Active
                          </span>
                        )}
                      </div>
                      <p className="mt-2 text-[11px] text-zinc-400 leading-relaxed">
                        Vector SVG renderer with gliding spring physics, resizable volume pane, and pure price-action focus.
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setChartEngine("canvas");
                        toast.info("Chart Engine set to Pro Canvas (TradingView)", "Chart Updated");
                      }}
                      className={`relative flex flex-col items-start rounded-lg border p-3.5 text-left transition-all cursor-pointer ${
                        chartEngine === "canvas"
                          ? "border-cyan-500 bg-cyan-500/10 ring-1 ring-cyan-500/30"
                          : "border-[#2b2b32] bg-[#131316] hover:border-zinc-700"
                      }`}
                    >
                      <div className="flex items-center justify-between w-full">
                        <div className="flex items-center gap-1.5">
                          <svg className="w-3.5 h-3.5 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                          </svg>
                          <span className="text-xs font-medium text-zinc-200">Pro Canvas</span>
                        </div>
                        {chartEngine === "canvas" && (
                          <span className="text-[10px] bg-cyan-500/20 text-cyan-400 px-1.5 py-0.5 rounded font-mono font-medium">
                            Active
                          </span>
                        )}
                      </div>
                      <p className="mt-2 text-[11px] text-zinc-400 leading-relaxed">
                        TradingView canvas with full technical indicators (RSI 14, MACD, Volume strip) and multi-timeframe overlays.
                      </p>
                    </button>
                  </div>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* Tab 2: AI Model & API Configuration */}
        {activeTab === "ai" && (
          <div className="mt-6 space-y-6">
            <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 shadow-sm">
              <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
                <div>
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                    Large Language Model & Strategy AI API
                  </h2>
                  <p className="mt-0.5 text-[11px] text-zinc-400">
                    Configure your LLM provider for natural language strategy synthesis, market analysis, and prompts.
                  </p>
                </div>
                {settings?.ai.configured ? (
                  <span className="text-[10px] bg-emerald-950/60 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded font-mono">
                    ✓ API Key Stored
                  </span>
                ) : (
                  <span className="text-[10px] bg-amber-950/60 text-amber-400 border border-amber-500/30 px-2 py-0.5 rounded font-mono">
                    No Key Configured
                  </span>
                )}
              </div>

              <div className="mt-5 grid gap-4 md:grid-cols-2">
                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    AI Provider
                  </label>
                  <select
                    value={aiProvider}
                    onChange={(e) => {
                      const p = e.target.value;
                      setAiProvider(p);
                      const defaults = modelOptions[p];
                      if (defaults && defaults.length > 0) setAiModel(defaults[0]);
                    }}
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500 font-sans"
                  >
                    <option value="gemini">Google Gemini (Gemini 2.5, 3.8, 3.7, 3.5, Flash, Pro)</option>
                    <option value="anthropic">Anthropic (Claude 3.7 Sonnet, Claude 3.5 Sonnet, Haiku)</option>
                    <option value="openai">OpenAI (ChatGPT, GPT-4o, o3-mini, o1)</option>
                    <option value="ollama">Ollama / Custom Local Endpoint (DeepSeek-R1, Qwen)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Model Selection
                  </label>
                  <div className="flex gap-2">
                    <select
                      value={aiModel}
                      onChange={(e) => setAiModel(e.target.value)}
                      className="flex-1 rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500 font-mono"
                    >
                      {modelOptions[aiProvider]?.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                    <input
                      type="text"
                      value={aiModel}
                      onChange={(e) => setAiModel(e.target.value)}
                      placeholder="Or custom model ID"
                      className="w-36 rounded-lg border border-[#2b2b32] bg-[#131316] px-2.5 py-2 text-xs text-zinc-300 font-mono outline-none focus:border-cyan-500"
                    />
                  </div>
                </div>

                <div className="md:col-span-2">
                  <div className="flex items-center justify-between mb-1">
                    <label className="block text-[11px] font-medium text-zinc-400">
                      {aiProvider === "ollama" ? "Ollama Host URL (e.g. http://localhost:11434)" : "Provider API Key"}
                    </label>
                    <button
                      type="button"
                      onClick={() => setShowAiKey(!showAiKey)}
                      className="text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors cursor-pointer"
                    >
                      {showAiKey ? "Hide Secret" : "Reveal / Edit Key"}
                    </button>
                  </div>
                  <div className="relative">
                    <input
                      type={showAiKey ? "text" : "password"}
                      value={aiApiKey}
                      onChange={(e) => setAiApiKey(e.target.value)}
                      placeholder={
                        aiProvider === "ollama"
                          ? "http://localhost:11434"
                          : "Paste API key (e.g. sk-proj-...)"
                      }
                      className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 font-mono outline-none focus:border-cyan-500"
                    />
                    {aiApiKey && aiApiKey.includes("...") && (
                      <span className="absolute right-2.5 top-2 text-[10px] text-zinc-500 font-mono">
                        (Encrypted Key Saved)
                      </span>
                    )}
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="block text-[11px] font-medium text-zinc-400">
                      Generation Temperature: <span className="text-zinc-200 font-mono">{aiTemperature}</span>
                    </label>
                    <span className="text-[10px] text-zinc-500">0.0 (Strict) - 1.0 (Creative)</span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={aiTemperature}
                    onChange={(e) => setAiTemperature(parseFloat(e.target.value))}
                    className="w-full accent-cyan-400 cursor-pointer"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Custom Prompt Prefix / System Directives (Optional)
                  </label>
                  <input
                    type="text"
                    value={aiSystemPrompt}
                    onChange={(e) => setAiSystemPrompt(e.target.value)}
                    placeholder="e.g. Always include 20-pip SL and 40-pip TP rules."
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              {/* Test Connection Banner */}
              {aiTestResult && (
                <div
                  className={`mt-4 rounded-lg p-3 text-xs flex items-center justify-between ${
                    aiTestResult.success
                      ? "bg-emerald-950/40 border border-emerald-500/30 text-emerald-300"
                      : "bg-red-950/40 border border-red-500/30 text-red-300"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span>{aiTestResult.success ? "✓" : "✗"}</span>
                    <span>{aiTestResult.message}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setAiTestResult(null)}
                    className="text-zinc-400 hover:text-white"
                  >
                    ×
                  </button>
                </div>
              )}

              <div className="mt-5 flex items-center justify-between pt-4 border-t border-[#26262b]">
                <button
                  type="button"
                  onClick={handleTestAi}
                  disabled={testingAi || (!aiApiKey && aiProvider !== "ollama")}
                  className="rounded-lg border border-[#373740] bg-[#222228] hover:bg-[#2c2c34] px-3.5 py-1.5 text-xs font-medium text-zinc-200 transition-colors disabled:opacity-40 flex items-center gap-2 cursor-pointer"
                >
                  {testingAi ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-zinc-200 border-t-transparent rounded-full animate-spin" />
                      <span>Verifying with {aiProvider}...</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-3.5 h-3.5 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                      <span>Test AI Connection</span>
                    </>
                  )}
                </button>

                <button
                  type="button"
                  onClick={handleSaveSettings}
                  disabled={saving}
                  className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-4 py-1.5 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer"
                >
                  {saving ? "Saving..." : "Save AI Configuration"}
                </button>
              </div>
            </section>
          </div>
        )}

        {/* Tab 3: Telegram Bot Configuration */}
        {activeTab === "telegram" && (
          <div className="mt-6 space-y-6">
            <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 shadow-sm">
              <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
                <div>
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                    Telegram Notification & Command Bot
                  </h2>
                  <p className="mt-0.5 text-[11px] text-zinc-400">
                    Receive instant trade proposal alerts, execution confirmations, daily digest summaries, and run emergency /kill commands.
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={tgEnabled}
                    onChange={(e) => setTgEnabled(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-zinc-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-zinc-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-500"></div>
                  <span className="ml-2 text-xs font-medium text-zinc-300">
                    {tgEnabled ? "Active" : "Disabled"}
                  </span>
                </label>
              </div>

              <div className="mt-5 grid gap-4 md:grid-cols-2">
                <div className="md:col-span-2">
                  <div className="flex items-center justify-between mb-1">
                    <label className="block text-[11px] font-medium text-zinc-400">
                      Telegram Bot Token
                    </label>
                    <button
                      type="button"
                      onClick={() => setShowTgToken(!showTgToken)}
                      className="text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors cursor-pointer"
                    >
                      {showTgToken ? "Hide Secret" : "Reveal / Edit Token"}
                    </button>
                  </div>
                  <div className="relative">
                    <input
                      type={showTgToken ? "text" : "password"}
                      value={tgToken}
                      onChange={(e) => setTgToken(e.target.value)}
                      placeholder="e.g. 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
                      className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 font-mono outline-none focus:border-cyan-500"
                    />
                    {tgToken && tgToken.includes("...") && (
                      <span className="absolute right-2.5 top-2 text-[10px] text-zinc-500 font-mono">
                        (Encrypted Token Saved)
                      </span>
                    )}
                  </div>
                  <span className="mt-1 block text-[10px] text-zinc-500">
                    Obtain your bot token from @BotFather on Telegram.
                  </span>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Target Chat ID
                  </label>
                  <input
                    type="text"
                    value={tgChatId}
                    onChange={(e) => setTgChatId(e.target.value)}
                    placeholder="e.g. 987654321 or -100123456789"
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 font-mono outline-none focus:border-cyan-500"
                  />
                  <span className="mt-1 block text-[10px] text-zinc-500">
                    User chat ID or group/channel ID for trade alerts.
                  </span>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Allowed User IDs Whitelist
                  </label>
                  <input
                    type="text"
                    value={tgAllowedUsers}
                    onChange={(e) => setTgAllowedUsers(e.target.value)}
                    placeholder="e.g. 987654321, 11223344"
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 font-mono outline-none focus:border-cyan-500"
                  />
                  <span className="mt-1 block text-[10px] text-zinc-500">
                    Comma-separated Telegram user IDs authorized to approve trades & trigger kill-switch.
                  </span>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Daily Digest Timezone
                  </label>
                  <select
                    value={tgTimezone}
                    onChange={(e) => setTgTimezone(e.target.value)}
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500 font-mono"
                  >
                    <option value="UTC">UTC</option>
                    <option value="America/New_York">America/New_York (EST/EDT)</option>
                    <option value="America/Chicago">America/Chicago (CST/CDT)</option>
                    <option value="Europe/London">Europe/London (GMT/BST)</option>
                    <option value="Europe/Frankfurt">Europe/Frankfurt (CET/CEST)</option>
                    <option value="Asia/Dubai">Asia/Dubai (GST)</option>
                    <option value="Asia/Singapore">Asia/Singapore (SGT)</option>
                    <option value="Asia/Tokyo">Asia/Tokyo (JST)</option>
                  </select>
                </div>
              </div>

              {/* Test Telegram Result Banner */}
              {tgTestResult && (
                <div
                  className={`mt-4 rounded-lg p-3 text-xs flex items-center justify-between ${
                    tgTestResult.success
                      ? "bg-emerald-950/40 border border-emerald-500/30 text-emerald-300"
                      : "bg-red-950/40 border border-red-500/30 text-red-300"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span>{tgTestResult.success ? "✓" : "✗"}</span>
                    <span>{tgTestResult.message}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setTgTestResult(null)}
                    className="text-zinc-400 hover:text-white"
                  >
                    ×
                  </button>
                </div>
              )}

              <div className="mt-5 flex items-center justify-between pt-4 border-t border-[#26262b]">
                <button
                  type="button"
                  onClick={handleTestTg}
                  disabled={testingTg || !tgToken || !tgChatId}
                  className="rounded-lg border border-[#373740] bg-[#222228] hover:bg-[#2c2c34] px-3.5 py-1.5 text-xs font-medium text-zinc-200 transition-colors disabled:opacity-40 flex items-center gap-2 cursor-pointer"
                >
                  {testingTg ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-zinc-200 border-t-transparent rounded-full animate-spin" />
                      <span>Dispatching Test Alert...</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-3.5 h-3.5 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                      </svg>
                      <span>Send Test Message</span>
                    </>
                  )}
                </button>

                <button
                  type="button"
                  onClick={handleSaveSettings}
                  disabled={saving}
                  className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-4 py-1.5 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer"
                >
                  {saving ? "Saving..." : "Save Telegram Configuration"}
                </button>
              </div>
            </section>
          </div>
        )}

        {activeTab === "notifications" && <PushNotifications />}

        {/* Tab 4: Engine & Risk Controls */}
        {activeTab === "risk" && (
          <div className="mt-6 space-y-6">
            <section className="rounded-xl border border-[#27272a] bg-[#18181b] p-5 shadow-sm">
              <div className="flex items-center justify-between pb-3 border-b border-[#26262b]">
                <div>
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                    Execution Mode & Engine Safety Boundaries
                  </h2>
                  <p className="mt-0.5 text-[11px] text-zinc-400">
                    Define maximum automated risk thresholds and execution behaviors.
                  </p>
                </div>
                <span className="text-[10px] font-mono text-zinc-500 uppercase">
                  Protective Circuit Breakers
                </span>
              </div>

              <div className="mt-5 grid gap-5 md:grid-cols-2">
                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Execution Mode
                  </label>
                  <select
                    value={executionMode}
                    onChange={(e) => setExecutionMode(e.target.value as "semi_auto" | "full_auto")}
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-cyan-500"
                  >
                    <option value="semi_auto">
                      Semi-Auto (Requires manual operator confirmation before placing live order)
                    </option>
                    <option value="full_auto">
                      Full-Auto (Signals trigger order execution automatically within risk limits)
                    </option>
                  </select>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Max Daily Drawdown Halt Threshold (%)
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      step="0.1"
                      min="0.5"
                      max="50"
                      value={maxDrawdown}
                      onChange={(e) => setMaxDrawdown(parseFloat(e.target.value) || 3.0)}
                      className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 font-mono outline-none focus:border-cyan-500"
                    />
                    <span className="text-xs font-mono text-zinc-400">%</span>
                  </div>
                  <span className="mt-1 block text-[10px] text-zinc-500">
                    Trading halts automatically for the session if cumulative losses exceed this limit.
                  </span>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Economic News Blackout Window (Minutes)
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      step="1"
                      min="0"
                      max="120"
                      value={blackoutMinutes}
                      onChange={(e) => setBlackoutMinutes(parseInt(e.target.value, 10) || 15)}
                      className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 font-mono outline-none focus:border-cyan-500"
                    />
                    <span className="text-xs font-mono text-zinc-400">min</span>
                  </div>
                  <span className="mt-1 block text-[10px] text-zinc-500">
                    Buffer before and after high-impact calendar news events during which new positions are suppressed.
                  </span>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                    Default Paper Account Starting Equity ($)
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      step="1000"
                      min="1000"
                      max="1000000"
                      value={paperEquity}
                      onChange={(e) => setPaperEquity(parseFloat(e.target.value) || 10000)}
                      className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-3 py-2 text-xs text-zinc-200 font-mono outline-none focus:border-cyan-500"
                    />
                    <span className="text-xs font-mono text-zinc-400">$</span>
                  </div>
                  <span className="mt-1 block text-[10px] text-zinc-500">
                    Simulation balance for testing deterministic strategies and paper portfolios.
                  </span>
                </div>
              </div>

              <div className="mt-5 flex items-center justify-end pt-4 border-t border-[#26262b]">
                <button
                  type="button"
                  onClick={handleSaveSettings}
                  disabled={saving}
                  className="rounded-lg bg-cyan-500 hover:bg-cyan-400 px-4 py-1.5 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer"
                >
                  {saving ? "Saving..." : "Save Risk Parameters"}
                </button>
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
