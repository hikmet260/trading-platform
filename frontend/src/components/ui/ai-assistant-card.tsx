"use client";

import React, { useState, useRef, useEffect } from "react";
import Link from "next/link";
import {
  ChartNetworkIcon,
  ImageIcon,
  MapIcon,
  PenToolIcon,
  ScanTextIcon,
  SparklesIcon,
  RotateCcw,
} from "lucide-react";

import { useTheme } from "@/context/ThemeContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export interface StrategyPayload {
  prompt?: string;
  symbol?: string;
  timeframe?: string;
  name?: string;
  confidence?: number;
  indicators_used?: string[];
  rationale?: string;
  target_url?: string;
  backtest_result?: {
    total_pnl_percent?: number;
    win_rate?: number;
    trade_count?: number;
    sharpe_ratio?: number;
    max_drawdown_percent?: number;
  };
  [key: string]: unknown;
}

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  action_type?: string;
  action_data?: StrategyPayload;
  model?: string;
  timestamp: string;
};

export interface AIAssistantCardProps {
  userName?: string;
  onClose?: () => void;
  onMinimize?: () => void;
  messages?: ChatMessage[];
  onSendMessage?: (message: string, model: string) => Promise<void> | void;
  loading?: boolean;
  onClearChat?: () => void;
  onSaveStrategy?: (promptStr: string) => void;
  onTriggerKillSwitch?: () => void;
  className?: string;
}

function renderFormattedInline(text: string, isDark: boolean): React.ReactNode {
  const parts = text.split(/(\*\*.*?\*\*|`.*?`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong
          key={i}
          className={`font-semibold ${isDark ? "text-zinc-100" : "text-zinc-900"}`}
        >
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={i}
          className={`font-mono text-[10.5px] px-1 py-0.5 rounded border ${
            isDark
              ? "bg-[#101014] text-cyan-300 border-cyan-500/20"
              : "bg-cyan-50 text-cyan-800 border-cyan-200"
          }`}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

function formatMessageContent(text: string, isDark: boolean): React.ReactNode {
  const lines = text.split("\n");
  return lines.map((line, idx) => {
    if (line.startsWith("### ")) {
      return (
        <h4
          key={idx}
          className={`text-xs font-semibold mt-2 mb-1 uppercase tracking-wider font-mono ${
            isDark ? "text-cyan-300" : "text-cyan-700"
          }`}
        >
          {line.replace("### ", "")}
        </h4>
      );
    }
    if (line.startsWith("## ")) {
      return (
        <h3
          key={idx}
          className={`text-xs font-bold mt-2 mb-1 ${
            isDark ? "text-zinc-100" : "text-zinc-900"
          }`}
        >
          {line.replace("## ", "")}
        </h3>
      );
    }
    if (line.trim().startsWith("- ") || line.trim().startsWith("* ")) {
      const content = line.trim().replace(/^[-*]\s+/, "");
      return (
        <div key={idx} className="flex items-start gap-1.5 my-0.5 pl-1">
          <span className="text-cyan-500 text-[10px] mt-0.5 shrink-0">•</span>
          <span className={isDark ? "text-zinc-200" : "text-zinc-700"}>
            {renderFormattedInline(content, isDark)}
          </span>
        </div>
      );
    }
    if (!line.trim()) {
      return <div key={idx} className="h-1.5" />;
    }
    return (
      <p
        key={idx}
        className={`my-0.5 leading-relaxed ${isDark ? "text-zinc-200" : "text-zinc-700"}`}
      >
        {renderFormattedInline(line, isDark)}
      </p>
    );
  });
}

export const Component = ({
  userName = "Trader",
  onClose,
  onMinimize,
  messages: controlledMessages,
  onSendMessage,
  loading: controlledLoading,
  onClearChat,
  onSaveStrategy,
  onTriggerKillSwitch,
  className = "",
}: AIAssistantCardProps) => {
  const { theme } = useTheme();
  const isDark = theme !== "light";

  const [promptText, setPromptText] = useState("");
  const [selectedModel, setSelectedModel] = useState("gemini-3.5-flash");
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const [localLoading, setLocalLoading] = useState(false);

  const messages = controlledMessages !== undefined ? controlledMessages : localMessages;
  const loading = controlledLoading !== undefined ? controlledLoading : localLoading;

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (messages.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading]);

  const promptBadges = [
    {
      label: "Create Strategy",
      icon: ImageIcon,
      color: isDark ? "text-cyan-400" : "text-cyan-600",
      prompt: "Draft an automated EMA crossover strategy for BTC/USDT with 0.5% risk",
    },
    {
      label: "Analyze Market",
      icon: ChartNetworkIcon,
      color: isDark ? "text-emerald-400" : "text-emerald-600",
      prompt: "Analyze the current trend and order book volume for BTC/USDT",
    },
    {
      label: "Make a Plan",
      icon: MapIcon,
      color: isDark ? "text-cyan-300" : "text-cyan-600",
      prompt: "Create a risk-managed daily trading plan with strict max drawdown limits",
    },
    {
      label: "Summarize P&L",
      icon: ScanTextIcon,
      color: isDark ? "text-rose-400" : "text-rose-600",
      prompt: "What is my current portfolio P&L, win rate, and open positions?",
    },
    {
      label: "Backtest Lab",
      icon: PenToolIcon,
      color: isDark ? "text-amber-400" : "text-amber-600",
      prompt: "Run a backtest on SOL/USDT 5m with RSI < 30 dip-buying rule",
    },
    {
      label: "Emergency Stop",
      icon: SparklesIcon,
      color: isDark ? "text-rose-500" : "text-rose-600",
      prompt: "Check emergency kill switch status and margin exposure",
    },
  ];

  const handleSend = async (textToSend?: string) => {
    const text = (textToSend || promptText).trim();
    if (!text || loading) return;

    setPromptText("");

    if (onSendMessage) {
      await onSendMessage(text, selectedModel);
      return;
    }

    // Fallback local simulation mode (for standalone demo.tsx)
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    setLocalMessages((prev) => [...prev, userMsg]);
    setLocalLoading(true);

    setTimeout(() => {
      const botMsg: ChatMessage = {
        id: `bot-${Date.now()}`,
        role: "assistant",
        content: `**Strategy Copilot Analysis**\n\nI processed: *"${text}"*.\n- Market depth: High liquidity detected\n- Volatility index: Normal\n- Recommended risk per trade: <= 1.0%\n\nReady to simulate historical backtest or deploy paper runner.`,
        model: selectedModel,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };
      setLocalMessages((prev) => [...prev, botMsg]);
      setLocalLoading(false);
    }, 800);
  };

  const handleClear = () => {
    if (onClearChat) {
      onClearChat();
    } else {
      setLocalMessages([]);
    }
  };

  const hasMessages = messages.length > 0;

  return (
    <Card
      className={`flex h-full min-h-[440px] sm:min-h-[580px] w-full max-w-[480px] flex-col gap-4 p-4 shadow-2xl transition-colors font-sans rounded-2xl ${
        isDark
          ? "bg-[#12131a]/98 border border-[#27272a] text-zinc-100 shadow-black/90 backdrop-blur-xl ring-1 ring-cyan-500/20"
          : "bg-white/98 border border-zinc-200 text-zinc-900 shadow-xl shadow-zinc-300/40 backdrop-blur-xl ring-1 ring-cyan-500/20"
      } ${className}`}
    >
      {/* Top Header Actions */}
      <div className="flex flex-row items-center justify-between pb-1 border-b border-inherit">
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full bg-cyan-400 animate-pulse shadow-[0_0_8px_rgba(6,182,212,0.8)]" />
          <span className="text-[11px] font-semibold text-cyan-500 tracking-wider uppercase font-mono">
            Copilot Core
          </span>
          <span
            className={`text-[9px] font-mono px-1.5 py-0.2 rounded border font-medium ${
              isDark
                ? "border-cyan-500/30 text-cyan-400 bg-cyan-950/40"
                : "border-cyan-200 text-cyan-700 bg-cyan-50"
            }`}
          >
            {selectedModel.replace("gemini-", "").replace("-flash", "")}
          </span>
        </div>

        <div className="flex items-center gap-1">
          {hasMessages && (
            <Button
              variant="ghost"
              size="icon"
              onClick={handleClear}
              title="Reset Conversation"
              className={`size-7 rounded-lg ${
                isDark
                  ? "text-zinc-400 hover:text-cyan-400 hover:bg-zinc-800"
                  : "text-zinc-500 hover:text-cyan-600 hover:bg-zinc-100"
              }`}
            >
              <RotateCcw className="size-3.5" />
            </Button>
          )}

          <Button
            variant="ghost"
            size="icon"
            onClick={onMinimize}
            title="Minimize"
            className={`size-7 rounded-lg ${
              isDark
                ? "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
                : "text-zinc-500 hover:text-zinc-800 hover:bg-zinc-100"
            }`}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="1em"
              height="1em"
              viewBox="0 0 24 24"
              className="size-3.5"
            >
              <path
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                d="M4 5a1 1 0 1 0 2 0a1 1 0 1 0-2 0m7 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0m7 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0M4 12a1 1 0 1 0 2 0a1 1 0 1 0-2 0m7 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0m7 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0M4 19a1 1 0 1 0 2 0a1 1 0 1 0-2 0m7 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0m7 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0"
              />
            </svg>
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            title="Close"
            className={`size-7 rounded-lg ${
              isDark
                ? "text-zinc-400 hover:text-rose-400 hover:bg-zinc-800"
                : "text-zinc-500 hover:text-rose-600 hover:bg-zinc-100"
            }`}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="size-3.5"
            >
              <path stroke="none" d="M0 0h24v24H0z" fill="none" />
              <path d="M18 6l-12 12" />
              <path d="M6 6l12 12" />
            </svg>
          </Button>
        </div>
      </div>

      <CardContent className="flex flex-1 flex-col p-0 justify-between overflow-hidden">
        {/* STATE A: WELCOME & PROMPT BADGES (When no messages sent yet) */}
        {!hasMessages && (
          <div className="flex flex-1 flex-col items-center justify-center space-y-5 py-4">
            {/* Prismatic SVG Icon with Cyan Glow */}
            <div className="relative group cursor-pointer transition-transform duration-300 hover:scale-105">
              <div className="absolute -inset-1 rounded-2xl bg-cyan-500/20 blur-md group-hover:bg-cyan-500/30 transition-all duration-300" />
              <svg
                fill="none"
                height="52"
                viewBox="0 0 48 48"
                width="52"
                xmlns="http://www.w3.org/2000/svg"
                xmlnsXlink="http://www.w3.org/1999/xlink"
                className="relative z-10"
              >
                <filter
                  id="a"
                  colorInterpolationFilters="sRGB"
                  filterUnits="userSpaceOnUse"
                  height="54"
                  width="48"
                  x="0"
                  y="-3"
                >
                  <feFlood floodOpacity="0" result="BackgroundImageFix" />
                  <feBlend
                    in="SourceGraphic"
                    in2="BackgroundImageFix"
                    mode="normal"
                    result="shape"
                  />
                  <feColorMatrix
                    in="SourceAlpha"
                    result="hardAlpha"
                    type="matrix"
                    values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
                  />
                  <feOffset dy="-3" />
                  <feGaussianBlur stdDeviation="1.5" />
                  <feComposite in2="hardAlpha" k2="-1" k3="1" operator="arithmetic" />
                  <feColorMatrix
                    type="matrix"
                    values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.1 0"
                  />
                  <feBlend in2="shape" mode="normal" result="effect1_innerShadow_3051_46851" />
                  <feColorMatrix
                    in="SourceAlpha"
                    result="hardAlpha"
                    type="matrix"
                    values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
                  />
                  <feOffset dy="3" />
                  <feGaussianBlur stdDeviation="1.5" />
                  <feComposite in2="hardAlpha" k2="-1" k3="1" operator="arithmetic" />
                  <feColorMatrix
                    type="matrix"
                    values="0 0 0 0 0.02 0 0 0 0 0.71 0 0 0 0 0.83 0 0 0 0.35 0"
                  />
                  <feBlend
                    in2="effect1_innerShadow_3051_46851"
                    mode="normal"
                    result="effect2_innerShadow_3051_46851"
                  />
                  <feColorMatrix
                    in="SourceAlpha"
                    result="hardAlpha"
                    type="matrix"
                    values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
                  />
                  <feMorphology
                    in="SourceAlpha"
                    operator="erode"
                    radius="1"
                    result="effect3_innerShadow_3051_46851"
                  />
                  <feOffset />
                  <feComposite in2="hardAlpha" k2="-1" k3="1" operator="arithmetic" />
                  <feColorMatrix
                    type="matrix"
                    values="0 0 0 0 0.0627451 0 0 0 0 0.0941176 0 0 0 0 0.156863 0 0 0 0.24 0"
                  />
                  <feBlend
                    in2="effect2_innerShadow_3051_46851"
                    mode="normal"
                    result="effect3_innerShadow_3051_46851"
                  />
                </filter>
                <filter
                  id="b"
                  colorInterpolationFilters="sRGB"
                  filterUnits="userSpaceOnUse"
                  height="42"
                  width="42"
                  x="3"
                  y="5.25"
                >
                  <feFlood floodOpacity="0" result="BackgroundImageFix" />
                  <feColorMatrix
                    in="SourceAlpha"
                    result="hardAlpha"
                    type="matrix"
                    values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
                  />
                  <feMorphology
                    in="SourceAlpha"
                    operator="erode"
                    radius="1.5"
                    result="effect1_dropShadow_3051_46851"
                  />
                  <feOffset dy="2.25" />
                  <feGaussianBlur stdDeviation="2.25" />
                  <feComposite in2="hardAlpha" operator="out" />
                  <feColorMatrix
                    type="matrix"
                    values="0 0 0 0 0.06 0 0 0 0 0.71 0 0 0 0 0.83 0 0 0 0.25 0"
                  />
                  <feBlend
                    in2="BackgroundImageFix"
                    mode="normal"
                    result="effect1_dropShadow_3051_46851"
                  />
                  <feBlend
                    in="SourceGraphic"
                    in2="effect1_dropShadow_3051_46851"
                    mode="normal"
                    result="shape"
                  />
                </filter>
                <linearGradient id="c" gradientUnits="userSpaceOnUse" x1="24" x2="26" y1=".000001" y2="48">
                  <stop offset="0" stopColor="#06b6d4" stopOpacity="0" />
                  <stop offset="1" stopColor="#06b6d4" stopOpacity=".18" />
                </linearGradient>
                <linearGradient id="d" gradientUnits="userSpaceOnUse" x1="24" x2="24" y1="6" y2="42">
                  <stop offset="0" stopColor="#06b6d4" stopOpacity=".95" />
                  <stop offset="1" stopColor="#10b981" stopOpacity=".7" />
                </linearGradient>
                <linearGradient id="e" gradientUnits="userSpaceOnUse" x1="24" x2="24" y1="0" y2="48">
                  <stop offset="0" stopColor="#06b6d4" stopOpacity=".2" />
                  <stop offset="1" stopColor="#10b981" stopOpacity="0" />
                </linearGradient>
                <clipPath id="f">
                  <rect height="48" rx="12" width="48" />
                </clipPath>
                <g filter="url(#a)">
                  <g clipPath="url(#f)">
                    <rect fill={isDark ? "#09090b" : "#f1f5f9"} height="48" rx="12" width="48" />
                    <path d="m0 0h48v48h-48z" fill="url(#c)" />
                    <g filter="url(#b)">
                      <path
                        clipRule="evenodd"
                        d="m6 24c11.4411 0 18-6.5589 18-18 0 11.4411 6.5589 18 18 18-11.4411 0-18 6.5589-18 18 0-11.4411-6.5589-18-18-18z"
                        fill="url(#d)"
                        fillRule="evenodd"
                      />
                    </g>
                  </g>
                  <rect height="46" rx="11" stroke="url(#e)" strokeWidth="2" width="46" x="1" y="1" />
                </g>
              </svg>
            </div>

            {/* Greeting Typography */}
            <div className="flex flex-col space-y-1.5 text-center px-4">
              <h2
                className={`text-xl font-medium tracking-tight ${
                  isDark ? "text-zinc-400" : "text-zinc-600"
                }`}
              >
                Hi {userName},
              </h2>
              <h3
                className={`text-lg font-semibold tracking-[-0.006em] ${
                  isDark ? "text-zinc-100" : "text-zinc-900"
                }`}
              >
                Welcome back! How can I help?
              </h3>
              <p
                className={`text-xs max-w-sm mx-auto leading-relaxed ${
                  isDark ? "text-zinc-400" : "text-zinc-500"
                }`}
              >
                Command strategy formulation, historical backtesting, portfolio audits, or live market scan below.
              </p>
            </div>

            {/* Quick Action Badges */}
            <div className="flex flex-wrap items-center justify-center gap-2 max-w-md px-2">
              {promptBadges.map((badge, idx) => {
                const Icon = badge.icon;
                return (
                  <Badge
                    key={idx}
                    variant="secondary"
                    onClick={() => handleSend(badge.prompt)}
                    className={`h-7 min-w-7 cursor-pointer gap-1.5 [&_svg]:-ms-px [&_svg]:shrink-0 text-xs [&_svg]:size-3.5 rounded-lg transition-all select-none active:scale-95 shadow-sm ${
                      isDark
                        ? "bg-[#18181b] hover:bg-[#222227] text-zinc-300 border border-[#27272a] hover:border-cyan-500/40 hover:text-cyan-400"
                        : "bg-zinc-100 hover:bg-zinc-200 text-zinc-700 border border-zinc-200 hover:border-cyan-500/50 hover:text-cyan-700"
                    }`}
                  >
                    <Icon aria-hidden="true" className={badge.color} />
                    <span>{badge.label}</span>
                  </Badge>
                );
              })}
            </div>
          </div>
        )}

        {/* STATE B: LIVE CONVERSATION STREAM (When user sent a message) */}
        {hasMessages && (
          <div className="flex-1 overflow-y-auto space-y-3 p-1 pr-2 text-xs">
            {messages.map((m) => {
              const isUser = m.role === "user";
              return (
                <div
                  key={m.id}
                  className={`flex flex-col ${isUser ? "items-end" : "items-start"}`}
                >
                  <div
                    className={`max-w-[88%] rounded-xl px-3.5 py-2.5 shadow-sm ${
                      isUser
                        ? isDark
                          ? "bg-cyan-950/40 border border-cyan-500/40 text-cyan-200"
                          : "bg-cyan-50 border border-cyan-300 text-cyan-950"
                        : isDark
                        ? "bg-[#18181b] border border-[#27272a] text-zinc-200"
                        : "bg-zinc-50 border border-zinc-200 text-zinc-900"
                    }`}
                  >
                    {isUser ? (
                      <p className="leading-relaxed whitespace-pre-wrap">{m.content}</p>
                    ) : (
                      formatMessageContent(m.content, isDark)
                    )}

                    {/* ACTION CARD: Strategy Card */}
                    {(() => {
                      const ruleObj = m.action_data?.rule as Record<string, unknown> | undefined;
                      const strategyPrompt =
                        (typeof m.action_data?.strategy_prompt === "string" && m.action_data.strategy_prompt) ||
                        (typeof m.action_data?.prompt === "string" && m.action_data.prompt) ||
                        (typeof ruleObj?.prompt === "string" && (ruleObj.prompt as string)) ||
                        m.content.match(/(?:BUY|SELL)\s+[A-Za-z0-9\/\-_]+\s+on\s+[0-9]+[mhd]\s+when\s+[^\n.]+/i)?.[0];

                      if (!strategyPrompt) return null;

                      return (
                        <div
                          className={`mt-2.5 pt-2 border-t rounded-lg p-2.5 ${
                            isDark
                              ? "border-cyan-500/30 bg-cyan-950/20"
                              : "border-cyan-200 bg-cyan-50/70"
                          }`}
                        >
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[10px] font-mono uppercase tracking-wider text-cyan-500 font-bold">
                              Synthesized Rule
                            </span>
                            {m.action_data?.confidence && (
                              <span className="text-[9px] font-mono text-emerald-500">
                                {(Number(m.action_data.confidence) * 100).toFixed(0)}% Confidence
                              </span>
                            )}
                          </div>
                          <div
                            className={`rounded p-2 font-mono text-[11px] mb-2 border break-words ${
                              isDark
                                ? "bg-black/60 text-cyan-300 border-cyan-500/30"
                                : "bg-white text-cyan-900 border-cyan-200"
                            }`}
                          >
                            {strategyPrompt}
                          </div>
                          {onSaveStrategy && (
                            <button
                              type="button"
                              onClick={() => onSaveStrategy(strategyPrompt)}
                              className="w-full rounded bg-cyan-500 hover:bg-cyan-400 py-1.5 text-center text-[11px] font-semibold text-zinc-950 transition-colors cursor-pointer shadow-md shadow-cyan-950/30 active:scale-[0.98]"
                            >
                              Deploy Strategy to Paper Engine
                            </button>
                          )}
                        </div>
                      );
                    })()}

                    {/* ACTION CARD: Backtest Result */}
                    {m.action_type === "BACKTEST" && m.action_data?.backtest_result && (() => {
                      const bt = m.action_data.backtest_result as Record<string, unknown>;
                      const pnl = (bt.total_return_pct ?? bt.total_pnl_percent ?? bt.total_pnl ?? 0) as number;
                      const winRate = (bt.win_rate_pct ?? bt.win_rate ?? 0) as number;
                      const trades = (bt.trade_count ?? 0) as number;
                      return (
                        <div
                          className={`mt-2.5 pt-2 border-t rounded-lg p-2.5 ${
                            isDark ? "border-[#27272a] bg-zinc-900/60" : "border-zinc-200 bg-zinc-100"
                          }`}
                        >
                          <span className="text-[10px] font-mono uppercase tracking-wider text-cyan-500 font-bold block mb-1">
                            Simulation Results ({trades} trades)
                          </span>
                          <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
                            <div className={`p-1.5 rounded ${isDark ? "bg-black/40" : "bg-white"}`}>
                              <span className="text-zinc-500 block">P&L</span>
                              <span
                                className={
                                  Number(pnl) >= 0
                                    ? "text-emerald-500 font-bold"
                                    : "text-rose-500 font-bold"
                                }
                              >
                                {Number(pnl) > 0 ? "+" : ""}
                                {Number(pnl).toFixed(2)}%
                              </span>
                            </div>
                            <div className={`p-1.5 rounded ${isDark ? "bg-black/40" : "bg-white"}`}>
                              <span className="text-zinc-500 block">Win Rate</span>
                              <span className="text-zinc-200 font-bold">
                                {Number(winRate).toFixed(1)}%
                              </span>
                            </div>
                          </div>
                        </div>
                      );
                    })()}

                    {/* ACTION CARD: Kill Switch Trigger */}
                    {m.action_type === "KILL_SWITCH" && onTriggerKillSwitch && (
                      <div
                        className={`mt-2.5 pt-2 border-t ${
                          isDark ? "border-[#27272a]" : "border-zinc-200"
                        }`}
                      >
                        <button
                          type="button"
                          onClick={onTriggerKillSwitch}
                          className="w-full rounded bg-rose-600 hover:bg-rose-500 py-1.5 text-center text-[11px] font-semibold text-white transition-colors cursor-pointer shadow-md shadow-rose-950/40"
                        >
                          Engage Emergency Kill Switch
                        </button>
                      </div>
                    )}

                    {/* ACTION CARD: Navigate Link */}
                    {m.action_type === "NAVIGATE" && m.action_data?.target_url && (
                      <div
                        className={`mt-2 pt-2 border-t ${
                          isDark ? "border-[#27272a]" : "border-zinc-200"
                        }`}
                      >
                        <Link
                          href={String(m.action_data.target_url)}
                          className="inline-flex items-center gap-1 text-[11px] text-cyan-500 hover:text-cyan-400 underline font-medium"
                        >
                          <span>Open {String(m.action_data.target_url)}</span>
                          <span>→</span>
                        </Link>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 mt-1 px-1">
                    <span
                      className={`text-[9px] font-mono ${
                        isDark ? "text-zinc-500" : "text-zinc-400"
                      }`}
                    >
                      {m.timestamp}
                    </span>
                    {m.model && (
                      <span className="text-[9px] text-cyan-500 font-mono">· {m.model}</span>
                    )}
                  </div>
                </div>
              );
            })}

            {loading && (
              <div
                className={`flex items-center gap-2 p-2 rounded-xl text-xs w-fit ${
                  isDark
                    ? "bg-[#18181b] border border-[#27272a] text-zinc-400"
                    : "bg-zinc-100 border border-zinc-200 text-zinc-600"
                }`}
              >
                <div className="w-3.5 h-3.5 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" />
                <span>Copilot is formulating response...</span>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Input Textarea Container with Electric Cyan Focus Ring */}
        <div
          className={`relative mt-2 flex-col rounded-xl transition-all shadow-inner ${
            isDark
              ? "bg-[#141416] ring-1 ring-[#27272a] focus-within:ring-2 focus-within:ring-cyan-500/60"
              : "bg-zinc-50 ring-1 ring-zinc-200 focus-within:ring-2 focus-within:ring-cyan-500/60"
          }`}
        >
          <div className="relative">
            <Textarea
              ref={textareaRef}
              value={promptText}
              onChange={(e) => setPromptText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              placeholder="Ask Copilot or give a command (e.g. 'Backtest RSI dips on BTC')..."
              className={`peer bg-transparent min-h-[72px] resize-none rounded-b-none border-none py-2.5 ps-9 pe-10 shadow-none text-xs focus-visible:outline-none focus-visible:ring-0 leading-relaxed font-sans ${
                isDark
                  ? "text-zinc-100 placeholder:text-zinc-500"
                  : "text-zinc-900 placeholder:text-zinc-400"
              }`}
            />

            {/* Search/Terminal Icon */}
            <div className="pointer-events-none absolute start-0 top-[11px] flex items-center justify-center ps-3 text-cyan-500/80 peer-disabled:opacity-50">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="1em"
                height="1em"
                viewBox="0 0 24 24"
                className="size-4"
              >
                <g fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="11.5" cy="11.5" r="9.5" />
                  <path strokeLinecap="round" d="M18.5 18.5L22 22" />
                </g>
              </svg>
            </div>

            {/* Audio Icon */}
            <button
              className={`absolute end-0 top-2.5 flex h-7 w-9 items-center justify-center rounded-e-md transition-colors outline-none focus:z-10 disabled:pointer-events-none disabled:opacity-50 ${
                isDark ? "text-zinc-500 hover:text-cyan-400" : "text-zinc-400 hover:text-cyan-600"
              }`}
              aria-label="Record audio"
              type="button"
              onClick={() => setPromptText("Analyze the live order book of BTC/USDT")}
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="1em"
                height="1em"
                viewBox="0 0 24 24"
                className="size-4"
              >
                <path
                  fill="currentColor"
                  fillRule="evenodd"
                  d="M5.25 8a6.75 6.75 0 0 1 13.5 0v5a6.75 6.75 0 0 1-13.5 0zM12 2.75A5.25 5.25 0 0 0 6.75 8v5a5.25 5.25 0 1 0 10.5 0V8c0-2.9-2.35-5.25-5.25-5.25m-1.485 4.295a.75.75 0 0 1-1.06-1.06l.534.504a37 37 0 0 1-.533-.505v-.001l.002-.002l.004-.003l.008-.008l.064-.06q.054-.047.139-.106c.113-.078.268-.167.473-.25c.41-.165 1.008-.304 1.854-.304s1.444.139 1.854.305c.205.083.36.17.473.249a2 2 0 0 1 .203.166l.008.008l.004.003l.001.002h.001c0 .001.001.002-.533.506l.534-.504a.75.75 0 0 1-1.068 1.055a1 1 0 0 0-.186-.095c-.207-.084-.61-.195-1.291-.195s-1.084.111-1.291.195a1 1 0 0 0-.194.1m0 3.001a.75.75 0 0 1-1.06-1.061L10 9.5a46 46 0 0 1-.544-.516v-.001l.002-.002l.004-.003l.008-.008l.064-.06q.054-.047.139-.106c.113-.078.268-.167.473-.25c.41-.165 1.008-.304 1.854-.304s1.444.139 1.854.305c.205.082.36.17.473.249a2 2 0 0 1 .203.166l.008.008l.004.003l.001.002h.001c0 .001.001.002-.544.517l.545-.515a.75.75 0 0 1-1.06 1.06l-.008-.005a1 1 0 0 0-.186-.095c-.207-.084-.61-.195-1.291-.195s-1.084.111-1.291.195a1 1 0 0 0-.186.095zm2.942-.029h-.001M3 10.25a.75.75 0 0 1 .75.75v2a8.25 8.25 0 0 0 16.5 0v-2a.75.75 0 0 1 1.5 0v2c0 5.385-4.365 9.75-9.75 9.75S2.25 18.385 2.25 13v-2a.75.75 0 0 1 .75-.75"
                  clipRule="evenodd"
                />
              </svg>
            </button>
          </div>

          {/* Bottom Action Footer: Model Dropdown & Send Button */}
          <div
            className={`flex items-center justify-between rounded-b-xl border-t px-3 py-1.5 ${
              isDark ? "bg-[#101012] border-[#26262b]" : "bg-zinc-100/80 border-zinc-200"
            }`}
          >
            <Select value={selectedModel} onValueChange={setSelectedModel}>
              <SelectTrigger
                size="sm"
                className={`h-7 text-xs w-[130px] transition-colors ${
                  isDark
                    ? "bg-[#18181b] border-[#27272a] text-zinc-200 hover:border-cyan-500/40"
                    : "bg-white border-zinc-300 text-zinc-800 hover:border-cyan-500/50"
                }`}
              >
                <SelectValue placeholder="Model" />
              </SelectTrigger>
              <SelectContent
                className={
                  isDark ? "bg-zinc-900 border-zinc-800 text-zinc-200" : "bg-white border-zinc-200 text-zinc-800"
                }
              >
                <SelectItem className="text-xs" value="gemini-3.5-flash">
                  Gemini 3.5 Flash
                </SelectItem>
                <SelectItem className="text-xs" value="gemini-3.5-flash-lite">
                  Gemini 3.5 Lite
                </SelectItem>
                <SelectItem className="text-xs" value="gemini-3.8-flash">
                  Gemini 3.8 Flash
                </SelectItem>
                <SelectItem className="text-xs" value="gpt-4o">
                  GPT-4o
                </SelectItem>
              </SelectContent>
            </Select>

            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                className={`h-7 px-2.5 gap-1.5 text-xs rounded-lg ${
                  isDark
                    ? "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60"
                    : "text-zinc-600 hover:text-zinc-900 hover:bg-zinc-200/60"
                }`}
                variant="ghost"
                onClick={() => setPromptText((prev) => prev + " [Attach: BTC/USDT 1m Chart]")}
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="1em"
                  height="1em"
                  viewBox="0 0 24 24"
                  className="size-3.5 text-cyan-500"
                >
                  <path
                    fill="currentColor"
                    d="M6.17 6.309a5.317 5.317 0 0 1 7.522 0a5.326 5.326 0 0 1 0 7.529l-1.43 1.43a.75.75 0 0 0 1.06 1.061l1.43-1.431a6.826 6.826 0 0 0 0-9.65a6.817 6.817 0 0 0-9.644 0l-2.86 2.864A6.826 6.826 0 0 0 6.69 19.749a.75.75 0 1 0 .083-1.498a5.326 5.326 0 0 1-3.465-9.08z"
                  />
                  <path
                    fill="currentColor"
                    d="M17.31 4.251a.75.75 0 0 0-.083 1.498a5.326 5.326 0 0 1 3.465 9.08L17.83 17.69a5.317 5.317 0 0 1-7.523 0a5.326 5.326 0 0 1 0-7.528l1.43-1.432a.75.75 0 0 0-1.06-1.06l-1.43 1.431a6.826 6.826 0 0 0 0 9.65a6.817 6.817 0 0 0 9.644 0l2.86-2.864A6.826 6.826 0 0 0 17.31 4.251"
                  />
                </svg>
                Attach
              </Button>

              <Button
                type="button"
                onClick={() => void handleSend()}
                disabled={!promptText.trim() || loading}
                className="h-7 px-3 gap-1 text-xs font-semibold rounded-lg bg-cyan-500 hover:bg-cyan-400 text-zinc-950 disabled:opacity-40 shadow-sm shadow-cyan-950/40 transition-all cursor-pointer"
              >
                Send
                <span className="font-mono text-[10px] text-zinc-950/80">↵</span>
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export const AIAssistantCard = Component;
export default Component;
