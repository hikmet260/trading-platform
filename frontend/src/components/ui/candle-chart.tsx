"use client";

import React from "react";
import { ChartSkeleton } from "./ChartSkeleton";

export type CustomCandle = {
  time?: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
  t?: number;
  o?: number;
  h?: number;
  l?: number;
  c?: number;
  v?: number;
};

export type CandleChartProps = {
  fill?: boolean;
  symbol: string;
  exchange: string;
  customCandles?: CustomCandle[];
  priceFmt?: (price: number) => string;
  onTimeframeChange?: (tf: string) => void;
  loading?: boolean;
};

export default function CandleChart({
  symbol,
  exchange,
  customCandles = [],
  priceFmt = (p) => `$${p.toFixed(2)}`,
  onTimeframeChange,
  loading = false,
}: CandleChartProps) {
  const [selectedTf, setSelectedTf] = React.useState("15m");
  const [hoverIndex, setHoverIndex] = React.useState<number | null>(null);

  const candles = React.useMemo(() => {
    return customCandles.slice(-60).map((c) => ({
      time: c.time ?? c.t ?? 0,
      open: c.open ?? c.o ?? 0,
      high: c.high ?? c.h ?? 0,
      low: c.low ?? c.l ?? 0,
      close: c.close ?? c.c ?? 0,
      volume: c.volume ?? c.v ?? 0,
    }));
  }, [customCandles]);

  if (loading || candles.length === 0) {
    return <ChartSkeleton symbol={symbol} exchange={exchange} />;
  }

  const minPrice = candles.length > 0 ? Math.min(...candles.map((c) => c.low)) : 0;
  const maxPrice = candles.length > 0 ? Math.max(...candles.map((c) => c.high)) : 100;
  const priceRange = maxPrice - minPrice || 1;

  const currentPrice = candles.length > 0 ? candles[candles.length - 1].close : 0;
  const prevPrice = candles.length > 1 ? candles[candles.length - 2].close : currentPrice;
  const changePct = prevPrice > 0 ? ((currentPrice - prevPrice) / prevPrice) * 100 : 0;

  const activeCandle = hoverIndex !== null && candles[hoverIndex] ? candles[hoverIndex] : null;
  const displayedCandle = activeCandle || (candles.length > 0 ? candles[candles.length - 1] : null);

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (candles.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const mouseX = ((e.clientX - rect.left) / rect.width) * 800;
    const clampedX = Math.max(20, Math.min(780, mouseX));
    const rawIdx = Math.round(((clampedX - 20) / 760) * (candles.length - 1));
    const idx = Math.max(0, Math.min(candles.length - 1, rawIdx));
    setHoverIndex(idx);
  };

  const handleMouseLeave = () => {
    setHoverIndex(null);
  };

  return (
    <div className="flex flex-col h-full w-full bg-[#121214] rounded-lg p-3 select-none">
      {/* Header */}
      <div className="flex items-center justify-between pb-2.5 border-b border-[#26262b]">
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-bold text-zinc-100">{symbol}</span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
            {exchange}
          </span>
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-sm font-semibold text-zinc-100">
              {priceFmt(currentPrice)}
            </span>
            <span
              className={`font-mono text-xs ${
                changePct >= 0 ? "text-emerald-400" : "text-red-400"
              }`}
            >
              {changePct >= 0 ? "+" : ""}
              {changePct.toFixed(2)}%
            </span>
          </div>
        </div>

        {/* Timeframe selector */}
        <div className="flex items-center gap-1 bg-[#18181b] p-0.5 rounded border border-[#27272a]">
          {["1m", "5m", "15m", "1h", "4h", "1d"].map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => {
                setSelectedTf(tf);
                onTimeframeChange?.(tf);
              }}
              className={`px-2 py-0.5 text-[10px] font-mono rounded transition-colors cursor-pointer ${
                selectedTf === tf
                  ? "bg-cyan-500 text-zinc-950 font-semibold shadow-xs"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50"
              }`}
            >
              {tf.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Interactive OHLCV Hover Bar */}
      {displayedCandle && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 px-2.5 mt-2 bg-[#141416] border border-[#27272a] rounded-md text-[11px] font-mono text-zinc-300">
          <span className="text-zinc-400 font-sans text-[10px] font-medium mr-1">
            {activeCandle ? "INSPECTING:" : "LATEST:"}
          </span>
          {displayedCandle.time > 0 && (
            <span className="text-zinc-400">
              {new Date(
                displayedCandle.time > 1e11 ? displayedCandle.time : displayedCandle.time * 1000
              ).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          )}
          <span>
            O:{" "}
            <span
              className={
                displayedCandle.close >= displayedCandle.open ? "text-emerald-400" : "text-rose-400"
              }
            >
              {priceFmt(displayedCandle.open)}
            </span>
          </span>
          <span>
            H: <span className="text-zinc-200">{priceFmt(displayedCandle.high)}</span>
          </span>
          <span>
            L: <span className="text-zinc-200">{priceFmt(displayedCandle.low)}</span>
          </span>
          <span>
            C:{" "}
            <span
              className={
                displayedCandle.close >= displayedCandle.open ? "text-emerald-400" : "text-rose-400"
              }
            >
              {priceFmt(displayedCandle.close)}
            </span>
          </span>
          {displayedCandle.volume > 0 && (
            <span>
              Vol:{" "}
              <span className="text-zinc-400">
                {displayedCandle.volume.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </span>
            </span>
          )}
        </div>
      )}

      {/* Candlestick Visualization */}
      <div className="flex-1 w-full relative min-h-[320px] mt-2 cursor-crosshair">
        {candles.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-zinc-500 font-mono">
            Awaiting streaming market tick data...
          </div>
        ) : (
          <svg
            className="w-full h-full"
            viewBox="0 0 800 300"
            preserveAspectRatio="none"
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
          >
            {candles.map((candle, idx) => {
              const x = (idx / Math.max(candles.length - 1, 1)) * 760 + 20;
              const isUp = candle.close >= candle.open;
              const color = isUp ? "#10b981" : "#f43f5e";

              const yHigh = 280 - ((candle.high - minPrice) / priceRange) * 260;
              const yLow = 280 - ((candle.low - minPrice) / priceRange) * 260;
              const yOpen = 280 - ((candle.open - minPrice) / priceRange) * 260;
              const yClose = 280 - ((candle.close - minPrice) / priceRange) * 260;

              const bodyTop = Math.min(yOpen, yClose);
              const bodyHeight = Math.max(Math.abs(yClose - yOpen), 2);
              const isHovered = hoverIndex === idx;

              return (
                <g key={candle.time || idx} className="transition-opacity">
                  {/* Subtle column highlight on hover */}
                  {isHovered && (
                    <rect
                      x={x - 6}
                      y={10}
                      width={12}
                      height={275}
                      fill="#06b6d4"
                      fillOpacity={0.12}
                      rx={2}
                    />
                  )}
                  <line
                    x1={x}
                    y1={yHigh}
                    x2={x}
                    y2={yLow}
                    stroke={color}
                    strokeWidth={isHovered ? 2.5 : 1.5}
                    opacity={hoverIndex !== null && !isHovered ? 0.45 : 0.9}
                  />
                  <rect
                    x={x - (isHovered ? 5 : 4)}
                    y={bodyTop}
                    width={isHovered ? 10 : 8}
                    height={bodyHeight}
                    fill={color}
                    rx={1}
                    opacity={hoverIndex !== null && !isHovered ? 0.45 : 1}
                  />
                </g>
              );
            })}

            {/* Hover Crosshairs */}
            {hoverIndex !== null && candles[hoverIndex] && (() => {
              const activeX = (hoverIndex / Math.max(candles.length - 1, 1)) * 760 + 20;
              const activeCloseY =
                280 - ((candles[hoverIndex].close - minPrice) / priceRange) * 260;
              return (
                <g pointerEvents="none">
                  {/* Vertical Guideline */}
                  <line
                    x1={activeX}
                    y1={10}
                    x2={activeX}
                    y2={285}
                    stroke="#06b6d4"
                    strokeWidth={1}
                    strokeDasharray="3 3"
                    opacity={0.6}
                  />
                  {/* Horizontal Guideline */}
                  <line
                    x1={20}
                    y1={activeCloseY}
                    x2={780}
                    y2={activeCloseY}
                    stroke="#06b6d4"
                    strokeWidth={1}
                    strokeDasharray="3 3"
                    opacity={0.5}
                  />
                  {/* Anchor Point on Candle Close */}
                  <circle
                    cx={activeX}
                    cy={activeCloseY}
                    r={3}
                    fill="#06b6d4"
                    stroke="#0b0c10"
                    strokeWidth={1.5}
                  />
                </g>
              );
            })()}
          </svg>
        )}
      </div>
    </div>
  );
}

export function Demo() {
  return null;
}
