"use client";

import { useEffect, useState, useTransition } from "react";
import { usePathname, useSearchParams } from "next/navigation";

// Global control functions for any component to trigger the top loader
export function startTopLoader(label?: string) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("trading:toploader:start", { detail: { label: label || "Loading..." } })
    );
  }
}

export function stopTopLoader() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("trading:toploader:stop"));
  }
}

export function TopProgressBar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [, startTransition] = useTransition();

  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [label, setLabel] = useState("Loading...");
  const [isLight, setIsLight] = useState(false);

  // Detect theme
  useEffect(() => {
    const checkTheme = () => {
      const root = document.documentElement;
      setIsLight(root.classList.contains("theme-white") || root.classList.contains("light"));
    };
    checkTheme();

    const observer = new MutationObserver(checkTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

    window.addEventListener("trading:theme-change", checkTheme);
    return () => {
      observer.disconnect();
      window.removeEventListener("trading:theme-change", checkTheme);
    };
  }, []);

  // Listen to manual start/stop events from AuthContext, Login, Logout, etc.
  useEffect(() => {
    const handleStart = (e: Event) => {
      const customEvent = e as CustomEvent<{ label?: string }>;
      setLabel(customEvent.detail?.label || "Loading...");
      setLoading(true);
      setProgress((prev) => (prev > 0 ? prev : 30));
    };

    const handleStop = () => {
      setProgress(100);
      const timer = setTimeout(() => {
        setLoading(false);
        setProgress(0);
        setLabel("Loading...");
      }, 350);
      return () => clearTimeout(timer);
    };

    window.addEventListener("trading:toploader:start", handleStart);
    window.addEventListener("trading:toploader:stop", handleStop);

    return () => {
      window.removeEventListener("trading:toploader:start", handleStart);
      window.removeEventListener("trading:toploader:stop", handleStop);
    };
  }, []);

  // When pathname or search params change, complete and reset the loader
  useEffect(() => {
    setProgress(100);
    const timer = setTimeout(() => {
      setLoading(false);
      setProgress(0);
      setLabel("Loading...");
    }, 350);
    return () => clearTimeout(timer);
  }, [pathname, searchParams]);

  // Intercept clicks on internal navigation links
  useEffect(() => {
    const handleAnchorClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const anchor = target?.closest("a") as HTMLAnchorElement | null;

      if (!anchor || !anchor.href) return;

      // Ignore if new tab / modifier keys
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        anchor.target === "_blank" ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.shiftKey
      ) {
        return;
      }

      // Check if same origin and internal path
      const currentUrl = new URL(window.location.href);
      const targetUrl = new URL(anchor.href, window.location.href);

      if (targetUrl.origin !== currentUrl.origin) return;

      // Ignore download or hash-only links on same path
      if (anchor.hasAttribute("download")) return;
      if (
        targetUrl.pathname === currentUrl.pathname &&
        targetUrl.search === currentUrl.search
      ) {
        return;
      }

      // Start the progress bar immediately
      startTransition(() => {
        setLabel("Loading...");
        setLoading(true);
        setProgress(25);
      });
    };

    document.addEventListener("click", handleAnchorClick, true);
    return () => document.removeEventListener("click", handleAnchorClick, true);
  }, []);

  // Animate progress incrementally while loading
  useEffect(() => {
    if (!loading) return;

    const t1 = setTimeout(() => setProgress((p) => (p < 55 ? 55 : p)), 100);
    const t2 = setTimeout(() => setProgress((p) => (p < 75 ? 75 : p)), 250);
    const t3 = setTimeout(() => setProgress((p) => (p < 88 ? 88 : p)), 500);

    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, [loading]);

  if (!loading && progress === 0) return null;

  return (
    <>
      {/* 1. Slim Horizontal Neon Progress Line */}
      <div
        aria-hidden="true"
        className="fixed top-0 left-0 right-0 z-[999999] h-[3px] pointer-events-none"
      >
        <div
          className="h-full bg-gradient-to-r from-cyan-500 via-teal-400 to-cyan-300 transition-all duration-300 ease-out shadow-[0_0_14px_#06b6d4,0_0_5px_#06b6d4]"
          style={{
            width: `${progress}%`,
            opacity: progress === 100 ? 0 : 1,
            transitionProperty: "width, opacity",
            transitionDuration: progress === 100 ? "350ms, 300ms" : "300ms, 0ms",
          }}
        />
      </div>

      {/* 2. Top-Center Floating Spinner Pill */}
      <div
        aria-live="polite"
        className="fixed top-2.5 left-1/2 -translate-x-1/2 z-[999999] pointer-events-none transition-all duration-200"
        style={{
          opacity: progress === 100 ? 0 : 1,
          transform: progress === 100 ? "translate(-50%, -6px)" : "translate(-50%, 0)",
        }}
      >
        <div
          className={`flex items-center gap-2 px-3 py-1.5 rounded-full border shadow-xl backdrop-blur-md text-xs font-mono font-medium tracking-tight ${
            isLight
              ? "bg-white/95 border-cyan-300 text-cyan-900 shadow-zinc-950/10"
              : "bg-[#18181b]/95 border-cyan-500/40 text-cyan-300 shadow-2xl shadow-cyan-950/50"
          }`}
        >
          {/* Circular Neon Spinner Wheel */}
          <div className="relative w-3.5 h-3.5 shrink-0">
            <div className="absolute inset-0 rounded-full border-2 border-cyan-500/20" />
            <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-cyan-400 border-r-cyan-400 animate-spin" />
          </div>
          <span>{label}</span>
        </div>
      </div>
    </>
  );
}
