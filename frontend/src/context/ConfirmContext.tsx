"use client";

import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from "react";
import { useTheme } from "@/context/ThemeContext";

export type ConfirmDetail = {
  label: string;
  value: string;
  badge?: "emerald" | "rose" | "cyan" | "red" | "blue" | "zinc";
};

export type ConfirmOptions = {
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "warning" | "primary" | "success";
  details?: ConfirmDetail[];
};

type ConfirmContextType = {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
};

const ConfirmContext = createContext<ConfirmContextType | undefined>(undefined);

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [modalState, setModalState] = useState<{
    isOpen: boolean;
    options: ConfirmOptions;
  }>({
    isOpen: false,
    options: { title: "", description: "" },
  });

  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise((resolve) => {
      resolverRef.current = resolve;
      setModalState({
        isOpen: true,
        options,
      });
    });
  }, []);

  const handleConfirm = useCallback(() => {
    setModalState((prev) => ({ ...prev, isOpen: false }));
    if (resolverRef.current) {
      resolverRef.current(true);
      resolverRef.current = null;
    }
  }, []);

  const handleCancel = useCallback(() => {
    setModalState((prev) => ({ ...prev, isOpen: false }));
    if (resolverRef.current) {
      resolverRef.current(false);
      resolverRef.current = null;
    }
  }, []);

  // Keyboard navigation: Escape to cancel, Enter to confirm
  useEffect(() => {
    if (!modalState.isOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        handleCancel();
      } else if (e.key === "Enter" && !e.shiftKey) {
        // Prevent default submission if focused on a textarea
        const target = e.target as HTMLElement;
        if (target.tagName !== "TEXTAREA") {
          e.preventDefault();
          handleConfirm();
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [modalState.isOpen, handleCancel, handleConfirm]);

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {modalState.isOpen && (
        <ConfirmModal
          options={modalState.options}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  const context = useContext(ConfirmContext);
  if (!context) {
    throw new Error("useConfirm must be used within a ConfirmProvider");
  }
  return context;
}

function ConfirmModal({
  options,
  onConfirm,
  onCancel,
}: {
  options: ConfirmOptions;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const {
    title,
    description,
    confirmLabel = "Confirm Action",
    cancelLabel = "Cancel",
    variant = "danger",
    details,
  } = options;

  let isLight = false;
  try {
    const themeContext = useTheme();
    isLight = themeContext.theme === "light";
  } catch {
    isLight = false;
  }

  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  // Auto focus confirm button when opened
  useEffect(() => {
    const timer = setTimeout(() => {
      confirmBtnRef.current?.focus();
    }, 50);
    return () => clearTimeout(timer);
  }, []);

  const variantStyles = {
    danger: {
      badgeBg: isLight
        ? "bg-rose-50 border-rose-200 text-rose-600"
        : "bg-rose-500/10 border-rose-500/30 text-rose-400",
      buttonBg: isLight
        ? "bg-rose-600 hover:bg-rose-500 !text-white shadow-sm"
        : "bg-rose-600 hover:bg-rose-500 !text-white shadow-lg shadow-rose-950/50",
      iconColor: isLight ? "text-rose-600" : "text-rose-400",
      icon: (
        <svg className={`w-5 h-5 ${isLight ? "text-rose-600" : "text-rose-400"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
      ),
    },
    warning: {
      badgeBg: isLight
        ? "bg-amber-50 border-amber-200 text-amber-700"
        : "bg-amber-500/10 border-amber-500/30 text-amber-400",
      buttonBg: isLight
        ? "bg-amber-600 hover:bg-amber-500 !text-white shadow-sm"
        : "bg-amber-600 hover:bg-amber-500 !text-white shadow-lg shadow-amber-950/50",
      iconColor: isLight ? "text-amber-700" : "text-amber-400",
      icon: (
        <svg className={`w-5 h-5 ${isLight ? "text-amber-700" : "text-amber-400"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    primary: {
      badgeBg: isLight
        ? "bg-cyan-50 border-cyan-200 text-cyan-700"
        : "bg-cyan-500/10 border-cyan-500/30 text-cyan-400",
      buttonBg: isLight
        ? "bg-cyan-600 hover:bg-cyan-500 !text-white shadow-sm font-semibold"
        : "bg-cyan-500 hover:bg-cyan-400 !text-zinc-950 font-semibold shadow-lg shadow-cyan-950/50",
      iconColor: isLight ? "text-cyan-700" : "text-cyan-400",
      icon: (
        <svg className={`w-5 h-5 ${isLight ? "text-cyan-700" : "text-cyan-400"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    success: {
      badgeBg: isLight
        ? "bg-emerald-50 border-emerald-200 text-emerald-700"
        : "bg-emerald-500/10 border-emerald-500/30 text-emerald-400",
      buttonBg: isLight
        ? "bg-emerald-600 hover:bg-emerald-500 !text-white shadow-sm"
        : "bg-emerald-600 hover:bg-emerald-500 !text-white shadow-lg shadow-emerald-950/50",
      iconColor: isLight ? "text-emerald-700" : "text-emerald-400",
      icon: (
        <svg className={`w-5 h-5 ${isLight ? "text-emerald-700" : "text-emerald-400"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
      ),
    },
  }[variant];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 font-sans"
    >
      {/* Blurred Backdrop */}
      <div
        onClick={onCancel}
        className={`fixed inset-0 transition-opacity duration-200 ${
          isLight ? "bg-zinc-950/40 backdrop-blur-sm" : "bg-black/75 backdrop-blur-md"
        }`}
      />

      {/* Modal Dialog Card */}
      <div
        className={`relative w-full max-w-md rounded-2xl border p-6 transition-all duration-150 ${
          isLight
            ? "border-zinc-200 bg-white text-zinc-900 shadow-xl shadow-zinc-950/10"
            : "border-[#27272a] bg-[#18181b] text-zinc-100 shadow-2xl shadow-black/90 ring-1 ring-white/10 backdrop-blur-xl"
        }`}
      >
        {/* Top Header with Icon */}
        <div className="flex items-start gap-3.5">
          <div className={`p-2.5 rounded-xl border shrink-0 ${variantStyles.badgeBg}`}>
            {variantStyles.icon}
          </div>

          <div className="flex-1 min-w-0 pt-0.5">
            <h3
              id="confirm-modal-title"
              className={`text-base font-semibold font-sans tracking-tight ${
                isLight ? "!text-zinc-900" : "!text-zinc-100"
              }`}
            >
              {title}
            </h3>
            <p
              className={`mt-1.5 text-xs font-sans leading-relaxed ${
                isLight ? "!text-zinc-600" : "!text-zinc-400"
              }`}
            >
              {description}
            </p>
          </div>

          <button
            type="button"
            onClick={onCancel}
            className={`rounded-lg p-1 transition-colors -mr-1 -mt-1 cursor-pointer ${
              isLight
                ? "!text-zinc-400 hover:!text-zinc-700 hover:bg-zinc-100"
                : "!text-zinc-500 hover:!text-zinc-300 hover:bg-[#27272a]"
            }`}
            aria-label="Close dialog"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Structured Details Grid (if provided) */}
        {details && details.length > 0 && (
          <div
            className={`mt-4 rounded-xl border p-3 text-xs space-y-2 ${
              isLight
                ? "border-zinc-200 bg-zinc-50/90 text-zinc-700"
                : "border-[#27272a] bg-[#141416] text-zinc-300"
            }`}
          >
            {details.map((d, i) => (
              <div key={i} className="flex items-center justify-between font-mono">
                <span
                  className={`text-[11px] font-sans ${
                    isLight ? "!text-zinc-500" : "!text-zinc-400"
                  }`}
                >
                  {d.label}
                </span>
                <span
                  className={`text-xs font-medium ${
                    d.badge === "emerald"
                      ? isLight ? "text-emerald-700 font-semibold" : "text-emerald-400 font-semibold"
                      : d.badge === "rose" || d.badge === "red"
                      ? isLight ? "text-rose-700 font-semibold" : "text-rose-400 font-semibold"
                      : d.badge === "cyan" || d.badge === "blue"
                      ? isLight ? "text-cyan-700 font-semibold" : "text-cyan-400 font-semibold"
                      : isLight ? "!text-zinc-900" : "!text-zinc-200"
                  }`}
                >
                  {d.value}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Action Buttons */}
        <div
          className={`mt-6 flex items-center justify-end gap-3 pt-4 border-t ${
            isLight ? "border-zinc-200" : "border-[#26262b]"
          }`}
        >
          <button
            type="button"
            onClick={onCancel}
            className={`rounded-lg px-4 py-2 text-xs font-medium transition-colors cursor-pointer ${
              isLight
                ? "border border-zinc-200 bg-white hover:bg-zinc-50 !text-zinc-700 shadow-sm"
                : "border border-[#27272a] bg-[#18181b] hover:bg-[#222227] !text-zinc-300"
            }`}
          >
            {cancelLabel}
          </button>

          <button
            ref={confirmBtnRef}
            type="button"
            onClick={onConfirm}
            className={`rounded-lg px-4 py-2 text-xs font-semibold transition-all cursor-pointer ${variantStyles.buttonBg}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
