"use client";

import React, { useState, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Sparkles } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { AIAssistantCard, ChatMessage } from "@/components/ui/ai-assistant-card";

export default function CopilotWidget() {
  const pathname = usePathname();
  const router = useRouter();
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  // Global hotkey: Cmd+K or Ctrl+K to toggle copilot
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setIsOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  if (pathname === "/login") return null;

  async function handleSend(textToSend: string, modelOverride?: string) {
    const text = textToSend.trim();
    if (!text || loading) return;

    const userMsgId = `user-${Date.now()}`;
    const userMsg: ChatMessage = {
      id: userMsgId,
      role: "user",
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const historyPayload = messages.slice(-6).map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const response = await apiFetch("/copilot/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: historyPayload,
          symbol: "BTC/USDT",
          timeframe: "1m",
          current_page: pathname,
          model: modelOverride,
        }),
      });

      if (!response.ok) {
        const errJson = await response.json().catch(() => ({}));
        throw new Error(errJson.detail || `Copilot error (${response.status})`);
      }

      const data = await response.json();
      const botMsg: ChatMessage = {
        id: `bot-${Date.now()}`,
        role: "assistant",
        content: data.reply || "Command executed.",
        action_type: data.action_type,
        action_data: data.action_data,
        model: data.model,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };

      setMessages((prev) => [...prev, botMsg]);

      if (data.action_type === "NAVIGATE" && data.action_data?.target_url) {
        const dest = data.action_data.target_url as string;
        if (dest && dest !== pathname) {
          router.push(dest);
        }
      }
    } catch (err) {
      const errorMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: "assistant",
        content: `⚠️ **Command Failed**: ${err instanceof Error ? err.message : "Network error"}`,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  }

  async function handleQuickSaveStrategy(promptStr: string) {
    try {
      const res = await apiFetch("/strategies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: `Copilot Rule ${new Date().toLocaleDateString()}`,
          prompt: promptStr,
          enabled: true,
        }),
      });
      if (!res.ok) throw new Error("Could not save strategy");
      toast.success("Strategy rule saved and activated in execution engine", "Strategy Saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to save strategy", "Save Error");
    }
  }

  async function handleTriggerKillSwitch() {
    const ok = await confirm({
      title: "Activate Emergency Kill Switch?",
      description: "This will halt all strategy execution and prevent any new orders from being placed.",
      confirmLabel: "Halt Execution",
      cancelLabel: "Cancel",
      variant: "danger",
    });
    if (!ok) return;

    try {
      const res = await apiFetch("/system/kill-switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: true }),
      });
      if (!res.ok) throw new Error("Failed to activate kill switch");
      toast.error("Emergency Kill Switch is ACTIVE. All engine execution is halted.", "Kill Switch Engaged");
      void handleSend("Report current system status");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Error engaging kill switch", "Action Failed");
    }
  }

  return (
    <>
      {/* 1. Minimized Floating Launcher Pill with Autohide to Right Edge */}
      {!isOpen && (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="fixed bottom-6 right-0 z-[90] flex items-center gap-2 rounded-l-full bg-[#2d2d34] hover:bg-[#383842] !text-white text-white border-l border-t border-b border-[#3f3f4a] pl-3 pr-4 py-2 shadow-2xl backdrop-blur-md cursor-pointer font-sans transition-all duration-300 ease-out translate-x-[calc(100%-40px)] hover:translate-x-0 focus:translate-x-0 focus-visible:translate-x-0 group"
          aria-label="Open AI Trading Copilot"
        >
          <Sparkles className="w-4 h-4 text-cyan-400 shrink-0 group-hover:scale-110 transition-transform duration-200" />
          <span
            className="text-xs font-semibold tracking-tight whitespace-nowrap !text-white text-white"
            style={{ color: "#ffffff" }}
          >
            AI Copilot
          </span>
          <span
            className="hidden sm:inline text-[10px] font-mono font-medium px-1.5 py-0.5 rounded border bg-zinc-800/90 border-zinc-600/60 !text-zinc-200 text-zinc-200 whitespace-nowrap"
            style={{ color: "#e4e4e7" }}
          >
            ⌘K
          </span>
        </button>
      )}

      {/* 2. Expanded Floating AI Assistant Card */}
      {isOpen && (
        <div className="fixed bottom-3 right-3 sm:bottom-5 sm:right-5 z-[90] flex flex-col w-[calc(100vw-24px)] sm:w-[440px] max-h-[88vh] animate-in fade-in slide-in-from-bottom-4 duration-200">
          <AIAssistantCard
            userName="Trader"
            onClose={() => setIsOpen(false)}
            onMinimize={() => setIsOpen(false)}
            messages={messages}
            loading={loading}
            onSendMessage={handleSend}
            onClearChat={() => setMessages([])}
            onSaveStrategy={handleQuickSaveStrategy}
            onTriggerKillSwitch={handleTriggerKillSwitch}
            className="h-[580px] max-h-[85vh]"
          />
        </div>
      )}
    </>
  );
}
