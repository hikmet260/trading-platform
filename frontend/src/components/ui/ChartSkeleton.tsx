"use client";

import React from "react";
import { useTheme } from "@/context/ThemeContext";

interface ChartSkeletonProps {
  symbol?: string;
  exchange?: string;
}

export function ChartSkeleton({
  symbol = "BTC/USDT",
  exchange = "BINANCE",
}: ChartSkeletonProps) {
  const { theme } = useTheme();
  const isLight = theme === "light" || theme === "white";

  return (
    <div
      className={`flex flex-col h-full w-full rounded-lg p-3 select-none transition-colors ${
        isLight
          ? "bg-white border border-zinc-200 text-zinc-900"
          : "bg-[#0f1016] border border-[#232634] text-zinc-100"
      }`}
    >
      {/* Top Header Placeholder (Clean high-contrast rounded pill blocks) */}
      <div
        className={`flex items-center justify-between pb-3 border-b ${
          isLight ? "border-zinc-200" : "border-[#232634]"
        }`}
      >
        <div className="flex items-center gap-2.5">
          {/* Symbol Badge */}
          <div
            className={`h-6 px-2.5 rounded-md flex items-center justify-center border shadow-xs ${
              isLight
                ? "bg-cyan-50 border-cyan-300 text-cyan-800"
                : "bg-cyan-950/60 border-cyan-500/50 text-cyan-400"
            }`}
          >
            <span className="text-[11px] font-mono font-bold tracking-tight">
              {symbol}
            </span>
          </div>

          {/* Exchange Badge */}
          <div
            className={`h-5 px-2 rounded-md flex items-center justify-center border ${
              isLight
                ? "bg-zinc-100 border-zinc-200 text-zinc-700"
                : "bg-[#1e2029] border-[#2e3245] text-zinc-200"
            }`}
          >
            <span className="text-[10px] font-mono font-semibold tracking-wide uppercase">
              {exchange}
            </span>
          </div>

          {/* Shimmer placeholders */}
          <div className="h-5 w-20 rounded-md youtube-skeleton" />
          <div className="h-4 w-12 rounded-md youtube-skeleton" />
        </div>

        {/* Timeframe block */}
        <div className="flex items-center gap-1.5">
          <div className="h-6 w-44 rounded-md youtube-skeleton" />
        </div>
      </div>

      {/* Main Chart Body (Large video/canvas skeleton with shimmer sweep) */}
      <div
        className={`flex-1 w-full min-h-[340px] mt-3 rounded-lg youtube-skeleton border relative flex items-center justify-center ${
          isLight ? "border-zinc-200" : "border-[#232634]"
        }`}
      >
        {/* Subtle grid guidelines for depth */}
        <div className="absolute inset-0 flex flex-col justify-between py-6 px-4 pointer-events-none opacity-30">
          <div
            className={`border-b border-dashed w-full ${
              isLight ? "border-zinc-300" : "border-[#2e3245]"
            }`}
          />
          <div
            className={`border-b border-dashed w-full ${
              isLight ? "border-zinc-300" : "border-[#2e3245]"
            }`}
          />
          <div
            className={`border-b border-dashed w-full ${
              isLight ? "border-zinc-300" : "border-[#2e3245]"
            }`}
          />
        </div>

        {/* Centered Loading Badge with Clean High-Contrast Text */}
        <div
          className={`relative z-10 flex items-center gap-2.5 px-4 py-2 rounded-xl shadow-2xl backdrop-blur-md border ${
            isLight
              ? "bg-white/95 border-cyan-300 text-zinc-900 shadow-zinc-300/40"
              : "bg-[#141520]/95 border-cyan-500/40 text-zinc-100 shadow-black/80"
          }`}
        >
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-cyan-500" />
          </span>
          <span
            className={`text-xs font-mono font-semibold tracking-tight ${
              isLight ? "text-zinc-900" : "text-zinc-100"
            }`}
          >
            Loading {symbol ? `${symbol}` : "market"} chart...
          </span>
        </div>
      </div>

      {/* Bottom Timeline Placeholder Bars */}
      <div className="flex items-center justify-between pt-3 px-4">
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <div key={i} className="h-2.5 w-10 rounded-sm youtube-skeleton opacity-70" />
        ))}
      </div>
    </div>
  );
}
export default ChartSkeleton;
