"use client";

import React, { createContext, useContext, useState, useCallback, useEffect } from "react";

export type ToastType = "success" | "error" | "info" | "warning";

export type Toast = {
  id: string;
  type: ToastType;
  title?: string;
  message: string;
  duration?: number;
};

type ToastContextType = {
  toasts: Toast[];
  toast: {
    success: (message: string, title?: string, duration?: number) => void;
    error: (message: string, title?: string, duration?: number) => void;
    info: (message: string, title?: string, duration?: number) => void;
    warning: (message: string, title?: string, duration?: number) => void;
  };
  removeToast: (id: string) => void;
};

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (type: ToastType, message: string, title?: string, duration = 4000) => {
      const id = `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
      const newToast: Toast = { id, type, title, message, duration };

      setToasts((prev) => [...prev, newToast]);

      if (duration > 0) {
        setTimeout(() => {
          removeToast(id);
        }, duration);
      }
    },
    [removeToast]
  );

  const toast = {
    success: (message: string, title?: string, duration?: number) =>
      addToast("success", message, title, duration),
    error: (message: string, title?: string, duration?: number) =>
      addToast("error", message, title, duration),
    info: (message: string, title?: string, duration?: number) =>
      addToast("info", message, title, duration),
    warning: (message: string, title?: string, duration?: number) =>
      addToast("warning", message, title, duration),
  };

  // Support global custom events (e.g. from apiFetch)
  useEffect(() => {
    function handleCustomToast(event: Event) {
      const customEvent = event as CustomEvent<{
        type: ToastType;
        message: string;
        title?: string;
        duration?: number;
      }>;
      if (customEvent.detail) {
        const { type, message, title, duration } = customEvent.detail;
        addToast(type || "info", message, title, duration);
      }
    }

    window.addEventListener("trading:toast", handleCustomToast);
    return () => window.removeEventListener("trading:toast", handleCustomToast);
  }, [addToast]);

  return (
    <ToastContext.Provider value={{ toasts, toast, removeToast }}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
}

function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}) {
  if (toasts.length === 0) return null;

  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 left-4 right-4 sm:left-auto sm:bottom-5 sm:right-5 z-[100] flex flex-col gap-2.5 sm:max-w-sm pointer-events-none"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: () => void;
}) {
  const styles = {
    success: {
      border: "border-emerald-500/40",
      iconColor: "text-emerald-400",
      titleColor: "text-emerald-400",
    },
    error: {
      border: "border-rose-500/40",
      iconColor: "text-rose-400",
      titleColor: "text-rose-400",
    },
    warning: {
      border: "border-amber-500/40",
      iconColor: "text-amber-400",
      titleColor: "text-amber-400",
    },
    info: {
      border: "border-cyan-500/40",
      iconColor: "text-cyan-400",
      titleColor: "text-cyan-400",
    },
  }[toast.type];

  return (
    <div
      role="alert"
      className={`pointer-events-auto rounded-lg border ${styles.border} bg-[#18181b] p-3 transition-colors flex items-start gap-2.5`}
    >
      <div className="flex-1 min-w-0">
        {toast.title && (
          <h4 className={`text-xs font-semibold ${styles.titleColor} font-sans leading-tight mb-0.5`}>
            {toast.title}
          </h4>
        )}
        <p className="text-xs text-zinc-300 font-sans leading-relaxed break-words">
          {toast.message}
        </p>
      </div>

      <button
        type="button"
        onClick={onDismiss}
        className="text-zinc-500 hover:text-zinc-300 rounded p-0.5 transition-colors"
        aria-label="Dismiss toast"
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}

// Helper to fire toasts from non-react contexts
export function fireToast(
  type: ToastType,
  message: string,
  title?: string,
  duration = 4000
) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("trading:toast", {
        detail: { type, message, title, duration },
      })
    );
  }
}
