"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";

export type Theme = "charcoal" | "white" | "dark" | "light";
export type ChartEngine = "ink" | "canvas";

type ThemeContextType = {
  theme: Theme;
  setTheme: (theme: Theme | "charcoal" | "white") => void;
  toggleTheme: () => void;
  chartEngine: ChartEngine;
  setChartEngine: (engine: ChartEngine) => void;
  toggleChartEngine: () => void;
};

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

const THEME_STORAGE_KEY = "trading_terminal_theme";
const CHART_ENGINE_STORAGE_KEY = "trading_terminal_chart_engine";

function normalizeTheme(raw: string | null): Theme {
  if (raw === "light" || raw === "white") return "light";
  return "dark";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("dark");
  const [chartEngine, setChartEngineState] = useState<ChartEngine>("ink");

  useEffect(() => {
    try {
      const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
      const normalized = normalizeTheme(savedTheme);
      setThemeState(normalized);
      applyThemeClass(normalized);

      const savedEngine = localStorage.getItem(CHART_ENGINE_STORAGE_KEY) as ChartEngine | null;
      if (savedEngine === "ink" || savedEngine === "canvas") {
        setChartEngineState(savedEngine);
      }
    } catch {
      applyThemeClass("dark");
    }
  }, []);

  function applyThemeClass(t: Theme) {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    if (t === "light") {
      root.classList.add("light", "theme-white");
      root.classList.remove("dark", "theme-charcoal");
      root.setAttribute("data-theme", "light");
    } else {
      root.classList.add("dark", "theme-charcoal");
      root.classList.remove("light", "theme-white");
      root.setAttribute("data-theme", "dark");
    }
    window.dispatchEvent(new CustomEvent("trading:theme-change", { detail: { theme: t } }));
  }

  const setTheme = useCallback((nextTheme: Theme | "charcoal" | "white") => {
    const normalized = normalizeTheme(nextTheme);
    setThemeState(normalized);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, normalized);
    } catch {
      // ignore
    }
    applyThemeClass(normalized);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  const setChartEngine = useCallback((nextEngine: ChartEngine) => {
    setChartEngineState(nextEngine);
    try {
      localStorage.setItem(CHART_ENGINE_STORAGE_KEY, nextEngine);
    } catch {
      // ignore
    }
    window.dispatchEvent(new CustomEvent("trading:chart-engine-change", { detail: { chartEngine: nextEngine } }));
  }, []);

  const toggleChartEngine = useCallback(() => {
    setChartEngine(chartEngine === "ink" ? "canvas" : "ink");
  }, [chartEngine, setChartEngine]);

  return (
    <ThemeContext.Provider
      value={{
        theme,
        setTheme,
        toggleTheme,
        chartEngine,
        setChartEngine,
        toggleChartEngine,
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}
