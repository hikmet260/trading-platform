"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
} from "lightweight-charts";
import { ema, macd, points, rsi, sma, type Candle } from "@/lib/indicators";
import { API, apiFetch, formatErrorDetail } from "@/lib/api";
import Navbar from "@/components/Navbar";
import CandleChart from "@/components/ui/candle-chart";
import { ChartSkeleton } from "@/components/ui/ChartSkeleton";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { useTheme } from "@/context/ThemeContext";
import { useConfirm } from "@/context/ConfirmContext";

const intervals: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
};

const AVAILABLE_MARKETS: Record<string, string[]> = {
  binance: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT"],
  bybit: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
  okx: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "OKB/USDT"],
  mt5: ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "NAS100"],
};

const POPULAR_PRESETS = [
  { symbol: "BTC/USDT", label: "BTC", exchange: "binance" },
  { symbol: "ETH/USDT", label: "ETH", exchange: "binance" },
  { symbol: "SOL/USDT", label: "SOL", exchange: "binance" },
  { symbol: "XAUUSD", label: "GOLD", exchange: "mt5" },
  { symbol: "EURUSD", label: "EUR", exchange: "mt5" },
];

type Trade = {
  id: string;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number | null;
  quantity: number;
  stop_loss: number;
  take_profit: number;
  pnl: number | null;
  status: string;
  mode: string;
  created_at: string;
};

type Strategy = {
  id: string;
  name: string;
  prompt: string;
  rule: Record<string, unknown>;
  enabled: boolean;
  broker_account_id: string | null;
  created_at: string;
};

export default function TradingDashboard() {
  const { user, token } = useAuth();
  const { toast } = useToast();
  const { theme, chartEngine, setChartEngine } = useTheme();
  const isLight = theme === "light" || theme === "white";
  const { confirm } = useConfirm();
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("1m");
  const [exchange, setExchange] = useState("binance");
  const [, setStatus] = useState("Connecting");
  const [latest, setLatest] = useState<number | null>(null);
  const [bars, setBars] = useState<Candle[]>([]);
  const [chartLoading, setChartLoading] = useState(true);
  const [switchingEngine, setSwitchingEngine] = useState(false);

  const switchChartEngine = (engine: "ink" | "canvas") => {
    if (engine === chartEngine) return;
    setSwitchingEngine(true);
    setChartEngine(engine);
    setTimeout(() => {
      setSwitchingEngine(false);
    }, 350);
  };

  const isChartSwitching = chartLoading || switchingEngine;
  const [system, setSystem] = useState<{
    kill_switch?: boolean;
    daily_drawdown_halted?: boolean;
    execution_mode?: string;
    live_order_api_enabled?: boolean;
    paper_mode?: boolean;
    engine_enabled?: boolean;
    engine_running?: boolean;
  } | null>(null);

  const [riskLimit, setRiskLimit] = useState("3");
  const [riskMessage, setRiskMessage] = useState("");

  // Indicators toggle
  const [showEma9, setShowEma9] = useState(true);
  const [showEma21, setShowEma21] = useState(true);
  const [showSma20, setShowSma20] = useState(true);
  const [showBollinger, setShowBollinger] = useState(true);
  const [showRsi, setShowRsi] = useState(true);
  const [showMacd, setShowMacd] = useState(true);

  // Right sidebar tab
  const [rightTab, setRightTab] = useState<"trade" | "strategy" | "risk">("trade");

  // Bottom dock tab
  const [dockTab, setDockTab] = useState<"positions" | "strategies" | "diagnostics">("positions");

  // Market picker popover state
  const [isMarketPickerOpen, setIsMarketPickerOpen] = useState(false);
  const [marketSearch, setMarketSearch] = useState("");
  const marketPickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (marketPickerRef.current && !marketPickerRef.current.contains(event.target as Node)) {
        setIsMarketPickerOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Trades and strategies list
  const [recentTrades, setRecentTrades] = useState<Trade[]>([]);
  const [savedStrategies, setSavedStrategies] = useState<Strategy[]>([]);
  const [lastSync, setLastSync] = useState<string | null>(null);

  // Chart DOM refs
  const priceChart = useRef<HTMLDivElement>(null);
  const rsiChart = useRef<HTMLDivElement>(null);
  const macdChart = useRef<HTMLDivElement>(null);

  // Chart instances
  const chartApis = useRef<IChartApi[]>([]);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema9SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const ema21SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const sma20SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const upperBbRef = useRef<ISeriesApi<"Line"> | null>(null);
  const lowerBbRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdLineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdSignalRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdHistRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const didFitContent = useRef(false);

  // Load auxiliary data
  const fetchAuxData = async () => {
    try {
      const [stateRes, riskRes, tradesRes, strategiesRes] = await Promise.all([
        apiFetch("/system/status", { cache: "no-store" }),
        apiFetch("/risk/settings", { cache: "no-store" }),
        apiFetch("/paper/trades?limit=25", { cache: "no-store" }),
        apiFetch("/strategies", { cache: "no-store" }),
      ]);

      if (stateRes.ok) setSystem(await stateRes.json());
      if (riskRes.ok) {
        const risk = await riskRes.json();
        setRiskLimit(String(risk.max_daily_drawdown_pct));
      }
      if (tradesRes.ok) setRecentTrades(await tradesRes.json());
      if (strategiesRes.ok) setSavedStrategies(await strategiesRes.json());
      setLastSync(new Date().toLocaleTimeString());
    } catch {
      // offline / unreachable
    }
  };

  useEffect(() => {
    setLastSync(new Date().toLocaleTimeString());
    void fetchAuxData();
    const timer = setInterval(() => void fetchAuxData(), 6000);
    return () => clearInterval(timer);
  }, []);

  async function toggleKillSwitch() {
    const activate = !system?.kill_switch;

    const confirmed = await confirm(
      activate
        ? {
            title: "Confirm Emergency System Halt",
            description:
              "You are about to activate the global Emergency Kill Switch. This will immediately freeze strategy runner execution and reject all incoming order routing.",
            confirmLabel: "Engage Emergency Halt",
            cancelLabel: "Dismiss",
            variant: "danger",
            details: [
              { label: "Execution Engine", value: "HALTED", badge: "red" },
              { label: "Active Orders", value: "Frozen / Blocked", badge: "red" },
              { label: "Operator", value: user?.email || "Local Admin" },
            ],
          }
        : {
            title: "Resume Execution Engine?",
            description:
              "You are about to disengage the emergency kill switch. Automated strategies and live order routing will be restored to normal operation.",
            confirmLabel: "Resume Runner",
            cancelLabel: "Keep Halted",
            variant: "primary",
            details: [
              { label: "Execution Engine", value: "ACTIVE", badge: "emerald" },
              { label: "Routing Status", value: "Live Market Access", badge: "emerald" },
            ],
          }
    );

    if (!confirmed) return;

    const response = await apiFetch("/system/kill-switch", {
      method: activate ? "POST" : "DELETE",
    });
    if (!response.ok) {
      setRiskMessage("Could not update emergency switch.");
      toast.error("Could not update emergency switch", "Kill Switch Failed");
      return;
    }
    setSystem((prev) => ({ ...(prev ?? {}), kill_switch: activate }));
    const msg = activate
      ? `Kill switch engaged. Stopped active orders.`
      : `Kill switch disengaged. Engine restored.`;
    setRiskMessage(msg);
    if (activate) {
      toast.warning(msg, "Emergency Halt Engaged");
    } else {
      toast.success(msg, "Engine Restored");
    }
    void fetchAuxData();
  }

  async function saveRiskLimit() {
    const response = await apiFetch("/risk/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        max_daily_drawdown_pct: Number(riskLimit),
        news_blackout_minutes: 15,
      }),
    });
    if (response.ok) {
      setRiskMessage("Risk parameters saved.");
      toast.success(`Max daily drawdown limit set to ${riskLimit}%`, "Risk Settings Saved");
    } else {
      setRiskMessage("Risk update rejected.");
      toast.error("Risk update rejected by engine", "Risk Update Failed");
    }
  }

  async function toggleStrategy(strat: Strategy) {
    const response = await apiFetch(`/strategies/${strat.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !strat.enabled }),
    });
    if (response.ok) {
      toast.success(
        `Strategy "${strat.name}" is now ${!strat.enabled ? "Active" : "Paused"}`,
        "Strategy Updated"
      );
      void fetchAuxData();
    } else {
      toast.error("Could not update strategy state", "Update Error");
    }
  }

  async function decideTrade(trade: Trade, approved: boolean) {
    const response = await apiFetch(`/paper/trades/${trade.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    if (response.ok) {
      toast.info(
        `Trade ${trade.symbol} (${trade.side.toUpperCase()}) was ${approved ? "Approved" : "Rejected"}`,
        "Decision Recorded"
      );
      void fetchAuxData();
    } else {
      toast.error("Failed to submit trade decision", "Decision Failed");
    }
  }

  // Indicator series computations
  const computed = useMemo(() => {
    const closes = bars.map((bar) => bar.close);
    const middle = sma(closes, 20);
    const deviations = closes.map((_, index) => {
      if (middle[index] === null) return null;
      const slice = closes.slice(index - 19, index + 1);
      const mean = middle[index] as number;
      return Math.sqrt(slice.reduce((sum, value) => sum + (value - mean) ** 2, 0) / 20);
    });
    return {
      ema9: points(bars, ema(closes, 9)),
      ema21: points(bars, ema(closes, 21)),
      sma20: points(bars, middle),
      upper: points(
        bars,
        middle.map((value, index) =>
          value === null || deviations[index] === null ? null : value + 2 * (deviations[index] as number)
        )
      ),
      lower: points(
        bars,
        middle.map((value, index) =>
          value === null || deviations[index] === null ? null : value - 2 * (deviations[index] as number)
        )
      ),
      rsi: points(bars, rsi(closes)),
      macd: macd(closes),
    };
  }, [bars]);

  const candleChartBars = useMemo(() => {
    return bars.map((b) => ({
      o: b.open,
      h: b.high,
      l: b.low,
      c: b.close,
      v: b.volume || 1,
      t: b.time * 1000,
    }));
  }, [bars]);

  // Fetch initial candles & connect real-time stream
  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    async function connect() {
      setChartLoading(true);
      didFitContent.current = false;
      const startTime = Date.now();
      setStatus("Loading market data");
      try {
        const query = new URLSearchParams({ symbol, timeframe, limit: "500", exchange });
        const endpoint =
          exchange === "mt5"
            ? `/market/mt5/${encodeURIComponent(symbol)}/candles?${query.toString()}`
            : `/market/crypto/candles?${query.toString()}`;
        const response = await apiFetch(endpoint, { cache: "no-store" });
        if (!response.ok) throw new Error(`Market data returned ${response.status}`);
        const payload = await response.json();
        const initial: Candle[] = payload.candles.map(
          (bar: { timestamp: number; open: number; high: number; low: number; close: number; volume: number }) => ({
            time: Math.floor(bar.timestamp / 1000),
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
          })
        );
        if (stopped) return;
        setBars(initial);
        if (initial.length > 0) {
          setLatest(initial[initial.length - 1].close);
        }
        setStatus("Live");
      } catch (error) {
        if (!stopped) setStatus(error instanceof Error ? error.message : "Market data unavailable");
      } finally {
        const elapsed = Date.now() - startTime;
        const minDelay = 250;
        if (elapsed < minDelay) {
          setTimeout(() => {
            if (!stopped) setChartLoading(false);
          }, minDelay - elapsed);
        } else {
          if (!stopped) setChartLoading(false);
        }
      }

      if (exchange === "mt5") connectMt5Polling();
      else connectStream();
    }

    function acceptTick(tick: { last?: number; bid?: number; ask?: number; timestamp?: number }) {
      const price =
        tick.last && tick.last > 0
          ? tick.last
          : tick.bid && tick.ask
          ? (tick.bid + tick.ask) / 2
          : undefined;
      if (price === undefined) return;
      setLatest(price);
      const period = intervals[timeframe] ?? 60;
      const time = Math.floor((tick.timestamp ?? Date.now()) / 1000 / period) * period;
      setBars((previous) => {
        if (!previous.length) return previous;
        const next = [...previous];
        const last = next[next.length - 1];
        if (time < last.time) return previous;
        if (time === last.time) {
          next[next.length - 1] = {
            ...last,
            high: Math.max(last.high, price),
            low: Math.min(last.low, price),
            close: price,
          };
        } else {
          next.push({
            time,
            open: last.close,
            high: price,
            low: price,
            close: price,
            volume: 0,
          });
        }
        return next.slice(-1000);
      });
    }

    function connectMt5Polling() {
      if (stopped) return;
      const poll = async () => {
        try {
          const response = await apiFetch(`/market/mt5/${encodeURIComponent(symbol)}/ticker`, {
            cache: "no-store",
          });
          if (response.ok) {
            acceptTick(await response.json());
            if (!stopped) setStatus("Live");
          }
        } catch {
          if (!stopped) setStatus("MT5 feed unavailable");
        }
      };
      void poll();
      reconnectTimer = setInterval(() => void poll(), 1000);
    }

    function connectStream() {
      if (stopped) return;
      const wsUrl = new URL(API || window.location.origin);
      wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
      wsUrl.pathname = "/ws/crypto/ticker";
      const params = new URLSearchParams({ exchange, symbol });
      if (token) params.set("token", token);
      wsUrl.search = params.toString();
      socket = new WebSocket(wsUrl);
      socket.onopen = () => {
        if (!stopped) setStatus("Live");
      };
      socket.onerror = () => {
        if (!stopped) setStatus("Reconnecting");
      };
      socket.onclose = () => {
        if (!stopped) reconnectTimer = setTimeout(connectStream, 2500);
      };
      socket.onmessage = (event) => {
        try {
          const tick = JSON.parse(event.data) as { last?: number; timestamp?: number };
          acceptTick(tick);
        } catch {
          // ignore
        }
      };
    }

    void connect();
    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [symbol, timeframe, exchange, token]);

  // Chart setup
  useEffect(() => {
    if (chartEngine !== "canvas") return;
    const refs = [priceChart.current, rsiChart.current, macdChart.current];
    if (refs.some((element) => !element)) return;

    // Theme palette
    const isWhite = theme === "light";
    const themeColors = {
      background: isWhite ? "#ffffff" : "#121214",
      text: isWhite ? "#52525b" : "#71717a",
      grid: isWhite ? "#f0f1f4" : "#1c1c21",
      border: isWhite ? "#e4e4e7" : "#26262b",
      crosshair: isWhite ? "#a1a1aa" : "#3f3f46",
    };

    const create = (element: HTMLDivElement, height: number) =>
      createChart(element, {
        width: element.clientWidth || 800,
        height,
        layout: {
          background: { color: themeColors.background },
          textColor: themeColors.text,
          fontFamily: "'Ubuntu', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: themeColors.grid },
          horzLines: { color: themeColors.grid },
        },
        rightPriceScale: {
          borderColor: themeColors.border,
          scaleMargins: { top: 0.1, bottom: 0.1 },
        },
        timeScale: {
          borderColor: themeColors.border,
          timeVisible: true,
          secondsVisible: false,
        },
        crosshair: {
          vertLine: { color: themeColors.crosshair, width: 1, style: 3 },
          horzLine: { color: themeColors.crosshair, width: 1, style: 3 },
        },
      });

    // 1. Price Chart
    const price = create(priceChart.current as HTMLDivElement, 420);
    const candles = price.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });
    candleSeriesRef.current = candles;

    const ema9 = price.addSeries(LineSeries, {
      color: "#f59e0b",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    ema9SeriesRef.current = ema9;

    const ema21 = price.addSeries(LineSeries, {
      color: "#60a5fa",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    ema21SeriesRef.current = ema21;

    const sma20 = price.addSeries(LineSeries, {
      color: "#93c5fd",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    sma20SeriesRef.current = sma20;

    const upperBb = price.addSeries(LineSeries, {
      color: "#52525b",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    upperBbRef.current = upperBb;

    const lowerBb = price.addSeries(LineSeries, {
      color: "#52525b",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    lowerBbRef.current = lowerBb;

    // 2. RSI Chart
    const rsiApi = create(rsiChart.current as HTMLDivElement, 100);
    const rsiSeries = rsiApi.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    rsiSeriesRef.current = rsiSeries;

    // 3. MACD Chart
    const macdApi = create(macdChart.current as HTMLDivElement, 110);
    const macdLine = macdApi.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    macdLineRef.current = macdLine;

    const macdSignal = macdApi.addSeries(LineSeries, {
      color: "#94a3b8",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    macdSignalRef.current = macdSignal;

    const macdHist = macdApi.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
    });
    macdHistRef.current = macdHist;

    const charts = [price, rsiApi, macdApi];
    chartApis.current = charts;

    // Synchronize time scales
    let isSyncing = false;
    charts.forEach((srcChart) => {
      srcChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (isSyncing || !range) return;
        isSyncing = true;
        charts.forEach((targetChart) => {
          if (targetChart !== srcChart) {
            targetChart.timeScale().setVisibleLogicalRange(range);
          }
        });
        isSyncing = false;
      });
    });

    const observer = new ResizeObserver(() => {
      charts.forEach((chart, index) => {
        const el = refs[index];
        if (el && el.clientWidth > 0) {
          chart.applyOptions({ width: el.clientWidth });
        }
      });
    });
    refs.forEach((element) => element && observer.observe(element));

    return () => {
      observer.disconnect();
      charts.forEach((chart) => {
        try {
          chart.remove();
        } catch {
          // ignore
        }
      });
      chartApis.current = [];
      candleSeriesRef.current = null;
      ema9SeriesRef.current = null;
      ema21SeriesRef.current = null;
      sma20SeriesRef.current = null;
      upperBbRef.current = null;
      lowerBbRef.current = null;
      rsiSeriesRef.current = null;
      macdLineRef.current = null;
      macdSignalRef.current = null;
      macdHistRef.current = null;
      didFitContent.current = false;
    };
  }, [chartEngine, theme]);

  // Synchronize chart theme with Charcoal / White
  useEffect(() => {
    if (!chartApis.current.length) return;
    const isWhite = theme === "light";
    const bg = isWhite ? "#ffffff" : "#121214";
    const text = isWhite ? "#52525b" : "#71717a";
    const grid = isWhite ? "#f0f1f4" : "#1c1c21";
    const border = isWhite ? "#e4e4e7" : "#26262b";

    chartApis.current.forEach((api) => {
      api.applyOptions({
        layout: {
          background: { color: bg },
          textColor: text,
        },
        grid: {
          vertLines: { color: grid },
          horzLines: { color: grid },
        },
        rightPriceScale: {
          borderColor: border,
        },
        timeScale: {
          borderColor: border,
        },
      });
    });
  }, [theme]);

  // Update chart data
  useEffect(() => {
    if (chartEngine !== "canvas" || !bars.length || !candleSeriesRef.current) return;
    const timeData = (pointsList: { time: number; value: number }[]) =>
      pointsList.map((point) => ({ time: point.time as never, value: point.value }));

    candleSeriesRef.current.setData(
      bars.map(({ time, open, high, low, close }) => ({
        time: time as never,
        open,
        high,
        low,
        close,
      })) as never[]
    );

    ema9SeriesRef.current?.setData(timeData(showEma9 ? computed.ema9 : []) as never[]);
    ema21SeriesRef.current?.setData(timeData(showEma21 ? computed.ema21 : []) as never[]);
    sma20SeriesRef.current?.setData(timeData(showSma20 ? computed.sma20 : []) as never[]);
    upperBbRef.current?.setData(timeData(showBollinger ? computed.upper : []) as never[]);
    lowerBbRef.current?.setData(timeData(showBollinger ? computed.lower : []) as never[]);
    rsiSeriesRef.current?.setData(timeData(showRsi ? computed.rsi : []) as never[]);
    macdLineRef.current?.setData(
      timeData(showMacd ? points(bars, computed.macd.line) : []) as never[]
    );
    macdSignalRef.current?.setData(
      timeData(showMacd ? points(bars, computed.macd.signal) : []) as never[]
    );
    macdHistRef.current?.setData(
      (showMacd
        ? bars.map((bar, index) => ({
            time: bar.time as never,
            value: computed.macd.histogram[index],
            color: computed.macd.histogram[index] >= 0 ? "#10b981" : "#ef4444",
          }))
        : []) as never[]
    );

    if (!didFitContent.current) {
      chartApis.current.forEach((chart) => chart.timeScale().fitContent());
      didFitContent.current = true;
    }
  }, [bars, computed, showEma9, showEma21, showSma20, showBollinger, showRsi, showMacd, chartEngine]);

  // Calculations for current bar metrics
  const lastBar = bars[bars.length - 1];
  const prevBar = bars[bars.length - 2];
  const priceChange =
    lastBar && prevBar ? lastBar.close - prevBar.close : 0;
  const priceChangePct =
    lastBar && prevBar ? (priceChange / prevBar.close) * 100 : 0;

  const currentRsi = computed.rsi.at(-1)?.value;
  const currentMacd = computed.macd.line.at(-1);

  return (
    <div className="min-h-screen bg-[#121214] text-[#f4f4f5] flex flex-col font-sans selection:bg-[#2e2e36] selection:text-white">
      {/* 1. TOP NAVIGATION BAR */}
      <Navbar
        extraControls={
          <div className="flex items-center gap-3">
            {/* Kill Switch Toggle Button */}
            <button
              onClick={() => void toggleKillSwitch()}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors border cursor-pointer ${
                system?.kill_switch
                  ? isLight
                    ? "bg-emerald-50 text-emerald-800 border-emerald-300 hover:bg-emerald-100"
                    : "bg-emerald-950/60 text-emerald-300 border-emerald-800/80 hover:bg-emerald-900/60"
                  : isLight
                  ? "bg-rose-50 text-rose-700 border-rose-300 hover:bg-rose-100"
                  : "bg-red-950/40 text-red-300 border-red-900/50 hover:bg-red-900/50"
              }`}
            >
              {system?.kill_switch ? "Resume Runner" : "Emergency Halt"}
            </button>
          </div>
        }
      />

      {/* 2. MARKET TICKER & TIMEFRAME BAR (CHARCOAL PRO) */}
      <div className="h-12 border-b border-[#26262b] bg-[#151518] px-4 flex items-center justify-between gap-4 text-xs overflow-x-auto no-scrollbar whitespace-nowrap">
        {/* Left: Active Market Selector & Quick Chips */}
        <div className="flex items-center gap-2.5 shrink-0">
          {/* Active Market Dropdown Trigger */}
          <div className="relative" ref={marketPickerRef}>
            <button
              type="button"
              onClick={() => setIsMarketPickerOpen(!isMarketPickerOpen)}
              className="flex items-center gap-2 rounded-lg border border-[#2b2b32] bg-[#1c1c20] hover:bg-[#232328] hover:border-zinc-600 px-2.5 py-1.5 text-xs transition-colors cursor-pointer"
            >
              <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-cyan-950/60 text-cyan-300 border border-cyan-500/30">
                {exchange.toUpperCase()}
              </span>
              <span className="font-mono font-semibold text-zinc-100">{symbol}</span>
              <svg
                className={`w-3 h-3 text-zinc-400 transition-transform ${isMarketPickerOpen ? "rotate-180" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* Dropdown Popover */}
            {isMarketPickerOpen && (
              <div className="absolute left-0 top-full mt-1.5 w-72 rounded-xl border border-[#27272a] bg-[#18181b] p-3 shadow-2xl shadow-black/90 ring-1 ring-white/10 z-50">
                {/* Search & Custom input */}
                <div className="mb-2">
                  <input
                    type="text"
                    value={marketSearch}
                    onChange={(e) => setMarketSearch(e.target.value)}
                    placeholder="Search pair or enter symbol..."
                    className="w-full rounded-lg border border-[#2b2b32] bg-[#131316] px-2.5 py-1.5 text-xs text-zinc-200 uppercase outline-none focus:border-cyan-500 font-mono"
                    autoFocus
                  />
                </div>

                {/* Exchange Tabs */}
                <div className="flex gap-1 mb-2 p-0.5 rounded-lg bg-[#131316] border border-[#26262b] text-[10px]">
                  {["binance", "bybit", "okx", "mt5"].map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      onClick={() => {
                        setExchange(ex);
                        if (ex === "mt5" && symbol.includes("/")) setSymbol("XAUUSD");
                        else if (ex !== "mt5" && !symbol.includes("/")) setSymbol("BTC/USDT");
                      }}
                      className={`flex-1 py-1 rounded text-center uppercase font-mono transition-colors cursor-pointer ${
                        exchange === ex
                          ? "bg-cyan-500 text-zinc-950 font-bold"
                          : "text-zinc-400 hover:text-zinc-200"
                      }`}
                    >
                      {ex === "mt5" ? "MT5" : ex}
                    </button>
                  ))}
                </div>

                {/* Filtered Pairs List */}
                <div className="max-h-48 overflow-y-auto space-y-1">
                  {AVAILABLE_MARKETS[exchange]
                    ?.filter((m) => m.toLowerCase().includes(marketSearch.toLowerCase()))
                    .map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => {
                          setSymbol(m);
                          setIsMarketPickerOpen(false);
                          setMarketSearch("");
                        }}
                        className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs font-mono transition-colors cursor-pointer ${
                          symbol === m
                            ? "bg-cyan-950/40 text-cyan-400 border border-cyan-500/40 font-medium"
                            : "text-zinc-300 hover:bg-[#232328]"
                        }`}
                      >
                        <span>{m}</span>
                        {symbol === m && <span className="text-[10px] text-cyan-400">✓ Active</span>}
                      </button>
                    ))}
                  {/* Custom symbol option */}
                  {marketSearch && !AVAILABLE_MARKETS[exchange]?.includes(marketSearch.toUpperCase()) && (
                    <button
                      type="button"
                      onClick={() => {
                        setSymbol(marketSearch.toUpperCase());
                        setIsMarketPickerOpen(false);
                        setMarketSearch("");
                      }}
                      className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs font-mono text-cyan-400 bg-cyan-950/40 border border-cyan-500/40 cursor-pointer"
                    >
                      <span>Custom: {marketSearch.toUpperCase()}</span>
                      <span className="text-[10px]">Select ↵</span>
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Quick Switch Chips */}
          <div className="flex items-center gap-1">
            {POPULAR_PRESETS.map((p) => {
              const isActive = symbol === p.symbol;
              return (
                <button
                  key={p.symbol}
                  type="button"
                  onClick={() => {
                    setSymbol(p.symbol);
                    setExchange(p.exchange);
                  }}
                  className={`px-2 py-1 rounded text-[11px] font-mono transition-colors cursor-pointer ${
                    isActive
                      ? "bg-[#27272f] text-cyan-400 font-semibold border border-cyan-500/40 shadow-xs"
                      : "text-zinc-400 hover:text-zinc-200 hover:bg-[#1c1c20]"
                  }`}
                  title={`${p.symbol} (${p.exchange})`}
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Center: Segmented Timeframe Controls */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="inline-flex rounded-lg border border-[#27272a] bg-[#131316] p-0.5">
            {Object.keys(intervals).map((tf) => (
              <button
                key={tf}
                type="button"
                onClick={() => setTimeframe(tf)}
                className={`px-2.5 py-1 text-xs font-mono rounded transition-colors cursor-pointer ${
                  timeframe === tf
                    ? "bg-[#27272f] text-cyan-400 font-semibold border border-cyan-500/40 shadow-xs"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>

        {/* Right: Structured Live Metric Ribbon */}
        <div className="flex items-center gap-4 text-xs font-mono shrink-0 ml-auto">
          {/* Price */}
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10px] text-zinc-500 uppercase font-sans tracking-wider">Price</span>
            {latest !== null ? (
              <span
                className={`text-sm font-semibold tracking-tight ${
                  priceChange >= 0 ? "text-emerald-400" : "text-red-400"
                }`}
              >
                {latest.toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 6,
                })}
              </span>
            ) : (
              <Skeleton className="h-4 w-16" />
            )}
          </div>

          {/* 24h Change */}
          <div
            className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium border ${
              priceChange >= 0
                ? "bg-emerald-950/40 text-emerald-300 border-emerald-500/30"
                : "bg-red-950/40 text-red-300 border-red-500/30"
            }`}
          >
            <span>{priceChange >= 0 ? "▲" : "▼"}</span>
            <span>{Math.abs(priceChangePct).toFixed(2)}%</span>
          </div>

          <div className="h-4 w-px bg-[#26262b] hidden sm:block" />

          {/* RSI */}
          <div className="hidden md:flex items-baseline gap-1.5">
            <span className="text-[10px] text-zinc-500 uppercase font-sans tracking-wider">RSI(14)</span>
            <span
              className={`text-xs ${
                currentRsi !== undefined
                  ? currentRsi >= 70
                    ? "text-red-400 font-semibold"
                    : currentRsi <= 30
                    ? "text-emerald-400 font-semibold"
                    : "text-zinc-300"
                  : "text-zinc-500"
              }`}
            >
              {currentRsi !== undefined ? currentRsi.toFixed(1) : "—"}
            </span>
          </div>

          <div className="h-4 w-px bg-[#26262b] hidden lg:block" />

          {/* MACD */}
          <div className="hidden lg:flex items-baseline gap-1.5">
            <span className="text-[10px] text-zinc-500 uppercase font-sans tracking-wider">MACD</span>
            <span
              className={`text-xs ${
                currentMacd !== undefined
                  ? currentMacd >= 0
                    ? "text-emerald-400"
                    : "text-red-400"
                  : "text-zinc-500"
              }`}
            >
              {currentMacd !== undefined
                ? currentMacd >= 0
                  ? `+${currentMacd.toFixed(3)}`
                  : currentMacd.toFixed(3)
                : "—"}
            </span>
          </div>
        </div>
      </div>

      {/* 3. MAIN WORKSPACE (CHART + ACTION TERMINAL) */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-0 border-b border-[#26262b]">
        {/* LEFT STAGE: CHARTS & OVERLAYS (8/9 COLS) */}
        <section className="lg:col-span-8 xl:col-span-9 border-r border-[#26262b] flex flex-col bg-[#121214]">
          {/* Chart Controls Strip (Horizontally Scrollable) */}
          <div className="border-b border-[#26262b] bg-[#161619] px-4 py-1.5 flex items-center justify-between gap-3 text-xs overflow-x-auto no-scrollbar whitespace-nowrap">
            <div className="flex items-center gap-3 shrink-0">
              {/* Presentation Mode: Ink Minimal vs Pro Canvas */}
              <div className="flex items-center gap-1 p-0.5 rounded-md bg-[#131316] border border-[#27272e] shrink-0">
                <button
                  type="button"
                  onClick={() => switchChartEngine("ink")}
                  className={`px-2.5 py-0.5 rounded text-[11px] font-medium font-sans transition-all cursor-pointer whitespace-nowrap ${
                    chartEngine === "ink"
                      ? "bg-cyan-500 hover:bg-cyan-400 text-zinc-950 font-bold shadow-sm"
                      : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60"
                  }`}
                >
                  Ink Terminal
                </button>
                <button
                  type="button"
                  onClick={() => switchChartEngine("canvas")}
                  className={`px-2.5 py-0.5 rounded text-[11px] font-medium font-sans transition-all cursor-pointer whitespace-nowrap ${
                    chartEngine === "canvas"
                      ? "bg-cyan-500 hover:bg-cyan-400 text-zinc-950 font-bold shadow-sm"
                      : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60"
                  }`}
                >
                  Pro Canvas
                </button>
              </div>

              {chartEngine === "canvas" && (
                <div className="flex items-center gap-2 border-l border-[#27272e] pl-3 shrink-0 whitespace-nowrap">
                  <span className="text-[11px] text-zinc-400 font-medium">Overlays:</span>
                  <button
                    onClick={() => setShowEma9(!showEma9)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-colors whitespace-nowrap ${
                      showEma9 ? "text-cyan-400 bg-cyan-950/40 border border-cyan-500/40" : "text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    EMA 9
                  </button>
                  <button
                    onClick={() => setShowEma21(!showEma21)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-colors whitespace-nowrap ${
                      showEma21 ? "text-cyan-400 bg-cyan-950/40 border border-cyan-500/40" : "text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    EMA 21
                  </button>
                  <button
                    onClick={() => setShowSma20(!showSma20)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-colors whitespace-nowrap ${
                      showSma20 ? "text-cyan-400 bg-cyan-950/40 border border-cyan-500/40" : "text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    SMA 20
                  </button>
                  <button
                    onClick={() => setShowBollinger(!showBollinger)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-colors whitespace-nowrap ${
                      showBollinger ? "text-cyan-400 bg-cyan-950/40 border border-cyan-500/40" : "text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    Bollinger (20,2)
                  </button>
                </div>
              )}
            </div>

            {chartEngine === "canvas" && (
              <div className="flex items-center gap-2 shrink-0 whitespace-nowrap">
                <button
                  onClick={() => setShowRsi(!showRsi)}
                  className={`px-2 py-0.5 rounded text-[11px] transition-colors whitespace-nowrap ${
                    showRsi ? "text-cyan-400 bg-cyan-950/40 border border-cyan-500/40" : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  RSI
                </button>
                <button
                  onClick={() => setShowMacd(!showMacd)}
                  className={`px-2 py-0.5 rounded text-[11px] transition-colors whitespace-nowrap ${
                    showMacd ? "text-cyan-400 bg-cyan-950/40 border border-cyan-500/40" : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  MACD
                </button>
                <button
                  onClick={() => chartApis.current.forEach((c) => c.timeScale().fitContent())}
                  className="ml-2 text-zinc-400 hover:text-zinc-200 transition-colors text-[11px] whitespace-nowrap cursor-pointer"
                >
                  Fit Chart
                </button>
              </div>
            )}
          </div>

          {/* Chart Display Area */}
          <div className="flex-1 flex flex-col relative min-h-[460px]">
            {isChartSwitching && (
              <div className="absolute inset-0 z-20 transition-opacity duration-150">
                <ChartSkeleton symbol={symbol} exchange={exchange.toUpperCase()} />
              </div>
            )}
            {chartEngine === "ink" ? (
              <div
                className={`flex-1 flex flex-col p-4 min-h-[460px] overflow-hidden ${
                  theme === "light" ? "bg-white" : "bg-[#121214]"
                }`}
              >
                <CandleChart
                  fill
                  symbol={symbol}
                  exchange={exchange.toUpperCase()}
                  customCandles={candleChartBars}
                  loading={isChartSwitching}
                  priceFmt={(p: number) =>
                    "$" +
                    p.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 4,
                    })
                  }
                  onTimeframeChange={(tf: string) => {
                    const mapped = tf === "1D" ? "1d" : tf === "1M" ? "1h" : tf === "5D" ? "4h" : "15m";
                    setTimeframe(mapped);
                  }}
                />
              </div>
            ) : (
              <div
                className={`flex-1 flex flex-col p-2 relative ${
                  theme === "light" ? "bg-white" : "bg-[#121214]"
                }`}
              >
                {isChartSwitching && (
                  <div className="absolute inset-0 z-20 transition-opacity duration-150">
                    <ChartSkeleton symbol={symbol} exchange={exchange.toUpperCase()} />
                  </div>
                )}
                <div ref={priceChart} className="w-full flex-1 min-h-[300px] sm:min-h-[400px]" />
                <div className={showRsi ? "mt-1 border-t border-[#26262b] pt-1" : "hidden"}>
                  <div className="px-2 text-[10px] text-zinc-400 font-mono">RSI (14)</div>
                  <div ref={rsiChart} className="w-full" />
                </div>
                <div className={showMacd ? "mt-1 border-t border-[#26262b] pt-1" : "hidden"}>
                  <div className="px-2 text-[10px] text-zinc-400 font-mono">MACD (12, 26, 9)</div>
                  <div ref={macdChart} className="w-full" />
                </div>
              </div>
            )}
          </div>
        </section>

        {/* RIGHT STAGE: ORDER EXECUTION & STRATEGY STUDIO (CHARCOAL) */}
        <aside className="lg:col-span-4 xl:col-span-3 flex flex-col bg-[#18181b]">
          {/* Tabs header */}
          <div className="flex border-b border-[#26262b] bg-[#151518]">
            <button
              onClick={() => setRightTab("trade")}
              className={`flex-1 py-2.5 text-xs font-medium transition-colors border-b-2 ${
                rightTab === "trade"
                  ? "border-cyan-500 text-cyan-400 bg-cyan-950/20"
                  : "border-transparent text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Order Ticket
            </button>
            <button
              onClick={() => setRightTab("strategy")}
              className={`flex-1 py-2.5 text-xs font-medium transition-colors border-b-2 ${
                rightTab === "strategy"
                  ? "border-cyan-500 text-cyan-400 bg-cyan-950/20"
                  : "border-transparent text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Strategy AI
            </button>
            <button
              onClick={() => setRightTab("risk")}
              className={`flex-1 py-2.5 text-xs font-medium transition-colors border-b-2 ${
                rightTab === "risk"
                  ? "border-cyan-500 text-cyan-400 bg-cyan-950/20"
                  : "border-transparent text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Risk Controls
            </button>
          </div>

          <div className="p-4 flex-1 overflow-y-auto">
            {/* TAB 1: ORDER EXECUTION TICKET */}
            {rightTab === "trade" && (
              <OrderTicket
                symbol={symbol}
                latestPrice={latest}
                paperMode={system?.paper_mode !== false}
                killSwitch={Boolean(system?.kill_switch)}
                onTradePlaced={() => void fetchAuxData()}
              />
            )}

            {/* TAB 2: STRATEGY STUDIO */}
            {rightTab === "strategy" && (
              <StrategyStudio
                symbol={symbol}
                timeframe={timeframe}
                candles={bars}
                paperMode={system?.paper_mode !== false}
                onSaved={() => void fetchAuxData()}
              />
            )}

            {/* TAB 3: RISK CONTROLS */}
            {rightTab === "risk" && (
              <div className="space-y-4">
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                    Risk Parameters
                  </h3>
                  <p className="mt-1 text-xs text-zinc-400">
                    Set deterministic execution loss thresholds and drawdown guardrails.
                  </p>
                </div>

                <div className="space-y-3 rounded-lg border border-[#27272a] bg-[#141416] p-3.5">
                  <div>
                    <label className="block text-[11px] font-medium text-zinc-400 mb-1">
                      Max Daily Drawdown (% of equity)
                    </label>
                    <div className="flex gap-2">
                      <input
                        type="number"
                        min="0.1"
                        max="100"
                        step="0.1"
                        value={riskLimit}
                        onChange={(e) => setRiskLimit(e.target.value)}
                        className="w-full rounded border border-[#27272a] bg-[#18181b] px-3 py-1.5 text-xs font-mono text-zinc-100 outline-none focus:border-cyan-500"
                      />
                      <button
                        onClick={() => void saveRiskLimit()}
                        className="rounded bg-[#18181b] hover:bg-[#222227] text-zinc-300 border border-[#27272a] px-3 py-1.5 text-xs transition-colors"
                      >
                        Save
                      </button>
                    </div>
                  </div>

                  <div className="border-t border-[#27272a] pt-3 text-xs space-y-1.5">
                    <div className="flex justify-between text-zinc-400 text-[11px]">
                      <span>Drawdown Circuit Breaker:</span>
                      <span className="text-zinc-200">
                        {system?.daily_drawdown_halted ? "HALTED" : "Normal"}
                      </span>
                    </div>
                    <div className="flex justify-between text-zinc-400 text-[11px]">
                      <span>Execution Engine:</span>
                      <span className="text-zinc-200">
                        {system?.execution_mode ?? "semi_auto"}
                      </span>
                    </div>
                    <div className="flex justify-between text-zinc-400 text-[11px]">
                      <span>Live Order API:</span>
                      <span className="text-zinc-200">
                        {system?.live_order_api_enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                  </div>
                </div>

                {riskMessage && (
                  <p className="text-xs text-cyan-400" role="status">
                    {riskMessage}
                  </p>
                )}
              </div>
            )}
          </div>
        </aside>
      </div>

      {/* 4. BOTTOM ACTIVITY DOCK (CHARCOAL BLACK) */}
      <section className="bg-[#151518] border-t border-[#26262b]">
        <div className="flex items-center justify-between border-b border-[#26262b] px-4 overflow-x-auto no-scrollbar gap-4">
          <div className="flex gap-4 shrink-0 whitespace-nowrap">
            <button
              onClick={() => setDockTab("positions")}
              className={`py-2 text-xs font-medium border-b-2 transition-colors ${
                dockTab === "positions"
                  ? "border-cyan-500 text-cyan-400"
                  : "border-transparent text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Recent Trades & Positions ({recentTrades.length})
            </button>
            <button
              onClick={() => setDockTab("strategies")}
              className={`py-2 text-xs font-medium border-b-2 transition-colors ${
                dockTab === "strategies"
                  ? "border-cyan-500 text-cyan-400"
                  : "border-transparent text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Active Strategies ({savedStrategies.length})
            </button>
            <button
              onClick={() => setDockTab("diagnostics")}
              className={`py-2 text-xs font-medium border-b-2 transition-colors ${
                dockTab === "diagnostics"
                  ? "border-cyan-500 text-cyan-400"
                  : "border-transparent text-zinc-400 hover:text-zinc-200"
              }`}
            >
              System Diagnostics
            </button>
          </div>

          <div className="text-[11px] text-zinc-500 font-mono shrink-0 hidden sm:block" suppressHydrationWarning>
            {lastSync ? `Last sync: ${lastSync}` : "Last sync: --:--:--"}
          </div>
        </div>

        <div className="p-4 max-h-60 overflow-y-auto">
          {dockTab === "positions" && (
            <div className="overflow-x-auto">
              {recentTrades.length === 0 ? (
                <p className="text-center py-6 text-xs text-zinc-500">
                  No active or recent trades recorded yet. Place an order or enable a strategy.
                </p>
              ) : (
                <table className="w-full min-w-[760px] text-left text-xs font-mono">
                  <thead className="text-[10px] text-zinc-400 uppercase font-sans border-b border-[#26262b]">
                    <tr>
                      <th className="pb-2">Time</th>
                      <th className="pb-2">Market</th>
                      <th className="pb-2">Side</th>
                      <th className="pb-2">Mode</th>
                      <th className="pb-2">Entry Price</th>
                      <th className="pb-2">Size</th>
                      <th className="pb-2">Stop Loss</th>
                      <th className="pb-2">Take Profit</th>
                      <th className="pb-2">P&L</th>
                      <th className="pb-2">Status</th>
                      <th className="pb-2 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#26262b]/60 text-zinc-300">
                    {recentTrades.map((t) => (
                      <tr key={t.id} className="hover:bg-[#1c1c20] transition-colors">
                        <td className="py-2 text-zinc-400" suppressHydrationWarning>
                          {new Date(t.created_at).toLocaleTimeString()}
                        </td>
                        <td className="py-2 text-zinc-200 font-sans font-medium">{t.symbol}</td>
                        <td className="py-2">
                          <span
                            className={
                              t.side.toLowerCase() === "buy" ? "text-emerald-400" : "text-red-400"
                            }
                          >
                            {t.side.toUpperCase()}
                          </span>
                        </td>
                        <td className="py-2 text-zinc-400 uppercase text-[10px] font-sans">
                          {t.mode}
                        </td>
                        <td className="py-2">{t.entry_price}</td>
                        <td className="py-2">{t.quantity.toFixed(5)}</td>
                        <td className="py-2 text-zinc-400">{t.stop_loss}</td>
                        <td className="py-2 text-zinc-400">{t.take_profit}</td>
                        <td
                          className={`py-2 ${
                            (t.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                          }`}
                        >
                          {t.pnl !== null ? t.pnl.toFixed(2) : "—"}
                        </td>
                        <td className="py-2 text-zinc-400">{t.status}</td>
                        <td className="py-2 text-right">
                          {t.status === "proposed" && (
                            <div className="flex justify-end gap-1.5">
                              <button
                                onClick={() => void decideTrade(t, true)}
                                className="px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/60 text-emerald-300 text-[11px] hover:bg-emerald-900/60"
                              >
                                Approve
                              </button>
                              <button
                                onClick={() => void decideTrade(t, false)}
                                className="px-2 py-0.5 rounded bg-[#27272a] text-zinc-300 text-[11px] hover:bg-[#323238]"
                              >
                                Skip
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {dockTab === "strategies" && (
            <div className="space-y-2">
              {savedStrategies.length === 0 ? (
                <p className="text-center py-6 text-xs text-zinc-500">
                  No strategies compiled or saved yet. Use the Strategy AI tab to compile rules.
                </p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  {savedStrategies.map((s) => (
                    <div
                      key={s.id}
                      className="flex items-center justify-between rounded-lg border border-[#27272a] bg-[#18181b] p-3"
                    >
                      <div>
                        <p className="text-xs font-medium text-zinc-200">{s.name}</p>
                        <p className="mt-0.5 text-[11px] text-zinc-500 truncate max-w-sm font-mono">
                          {s.prompt}
                        </p>
                      </div>
                      <button
                        onClick={() => void toggleStrategy(s)}
                        className={`px-3 py-1 rounded text-xs transition-colors ${
                          s.enabled
                            ? "bg-cyan-950/40 text-cyan-400 border border-cyan-500/40"
                            : "bg-[#27272a] text-zinc-400 hover:text-zinc-200"
                        }`}
                      >
                        {s.enabled ? "Active" : "Paused"}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {dockTab === "diagnostics" && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
              <div className="rounded border border-[#27272a] bg-[#18181b] p-3">
                <span className="text-[10px] text-zinc-400 uppercase">Engine State</span>
                <p className="mt-1 font-medium text-zinc-200">
                  {system?.kill_switch
                    ? "Halted (Kill Switch)"
                    : system?.daily_drawdown_halted
                    ? "Halted (Loss Limit)"
                    : "Operational"}
                </p>
              </div>
              <div className="rounded border border-[#27272a] bg-[#18181b] p-3">
                <span className="text-[10px] text-zinc-400 uppercase">Execution Mode</span>
                <p className="mt-1 font-medium text-zinc-200">{system?.execution_mode ?? "semi_auto"}</p>
              </div>
              <div className="rounded border border-[#27272a] bg-[#18181b] p-3">
                <span className="text-[10px] text-zinc-400 uppercase">Trading Mode</span>
                <p className="mt-1 font-medium text-zinc-200">
                  {system?.paper_mode === false ? "Live Broker" : "Simulated Ledger"}
                </p>
              </div>
              <div className="rounded border border-[#27272a] bg-[#18181b] p-3">
                <span className="text-[10px] text-zinc-400 uppercase">Live Orders API</span>
                <p className="mt-1 font-medium text-zinc-200">
                  {system?.live_order_api_enabled ? "Enabled" : "Disabled"}
                </p>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

// ----------------------------------------------------------------------
// SUB-COMPONENT: ORDER EXECUTION TICKET (CHARCOAL BLACK)
// ----------------------------------------------------------------------
function OrderTicket({
  symbol,
  latestPrice,
  paperMode,
  killSwitch,
  onTradePlaced,
}: {
  symbol: string;
  latestPrice: number | null;
  paperMode: boolean;
  killSwitch: boolean;
  onTradePlaced: () => void;
}) {
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const [oneClickTrading, setOneClickTrading] = useState(false);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [entryPrice, setEntryPrice] = useState<string>("");
  const [stopLoss, setStopLoss] = useState<string>("");
  const [takeProfit, setTakeProfit] = useState<string>("");
  const [riskPct, setRiskPct] = useState<number>(1.0);
  const [accountEquity] = useState<number>(10000);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  useEffect(() => {
    if (latestPrice && orderType === "market") {
      setEntryPrice(String(latestPrice));
      const defaultSl =
        side === "buy" ? latestPrice * 0.98 : latestPrice * 1.02;
      const defaultTp =
        side === "buy" ? latestPrice * 1.04 : latestPrice * 0.96;
      setStopLoss((prev) => (prev ? prev : defaultSl.toFixed(2)));
      setTakeProfit((prev) => (prev ? prev : defaultTp.toFixed(2)));
    }
  }, [latestPrice, orderType, side]);

  const priceNum = Number(entryPrice) || (latestPrice ?? 0);
  const stopNum = Number(stopLoss) || 0;
  const tpNum = Number(takeProfit) || 0;
  const stopDistance = Math.abs(priceNum - stopNum);
  const riskAmount = accountEquity * (riskPct / 100);
  const estimatedQty = stopDistance > 0 ? riskAmount / stopDistance : 0;
  const rewardDistance = Math.abs(tpNum - priceNum);
  const rrRatio = stopDistance > 0 ? (rewardDistance / stopDistance).toFixed(2) : "0";

  async function submitOrder() {
    setBusy(true);
    setMessage(null);

    if (killSwitch) {
      setMessage({ text: "Cannot place order: Emergency kill switch is active.", error: true });
      setBusy(false);
      return;
    }

    if (!oneClickTrading) {
      const confirmed = await confirm({
        title: `Confirm ${side.toUpperCase()} Order`,
        description: `Verify your order parameters before submitting to the ${paperMode ? "paper simulation ledger" : "live broker"}.`,
        confirmLabel: `Submit ${side.toUpperCase()} Order`,
        cancelLabel: "Edit Order",
        variant: side === "buy" ? "success" : "danger",
        details: [
          { label: "Market", value: symbol, badge: "blue" },
          { label: "Order Side", value: side.toUpperCase(), badge: side === "buy" ? "emerald" : "red" },
          {
            label: "Execution Price",
            value: `$${priceNum.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 6,
            })}`,
          },
          { label: "Calculated Position Size", value: `${estimatedQty.toFixed(4)} units` },
          {
            label: "Stop Loss",
            value: stopNum
              ? `$${stopNum.toLocaleString(undefined, { minimumFractionDigits: 2 })}`
              : "None (Uncapped Risk)",
          },
          {
            label: "Take Profit",
            value: tpNum
              ? `$${tpNum.toLocaleString(undefined, { minimumFractionDigits: 2 })}`
              : "None",
          },
          {
            label: "Risk Budget",
            value: `${riskPct}% of Equity ($${riskAmount.toFixed(2)})`,
          },
        ],
      });

      if (!confirmed) {
        setBusy(false);
        return;
      }
    }

    try {
      const payload = {
        symbol,
        side,
        entry_price: priceNum,
        stop_loss: stopNum || null,
        take_profit: tpNum || null,
        risk_pct: riskPct / 100,
        account_equity: accountEquity,
      };

      const response = await apiFetch("/paper/trades", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const resData = await response.json();
      if (!response.ok) {
        throw new Error(formatErrorDetail(resData.detail, "Order failed"));
      }

      const summary = `${side.toUpperCase()} ${estimatedQty.toFixed(4)} ${symbol} @ ${priceNum}`;
      setMessage({
        text: `Order submitted (${resData.status}): ${summary}`,
        error: false,
      });
      toast.success(summary, `Order Placed (${resData.status})`);
      onTradePlaced();
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Order rejected";
      setMessage({
        text: errMsg,
        error: true,
      });
      toast.error(errMsg, "Order Rejected");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Side Selector Tabs (Long / Short) */}
      <div className="grid grid-cols-2 gap-1 rounded bg-[#141416] p-1 border border-[#27272a]">
        <button
          type="button"
          onClick={() => setSide("buy")}
          className={`py-1.5 text-xs font-medium rounded transition-colors ${
            side === "buy"
              ? "bg-emerald-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Buy / Long
        </button>
        <button
          type="button"
          onClick={() => setSide("sell")}
          className={`py-1.5 text-xs font-medium rounded transition-colors ${
            side === "sell"
              ? "bg-red-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Sell / Short
        </button>
      </div>

      {/* Order Type Toggle */}
      <div className="flex items-center justify-between text-xs text-zinc-400">
        <span>Order Type</span>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => setOrderType("market")}
            className={`px-2 py-0.5 rounded ${
              orderType === "market"
                ? "bg-[#27272a] text-zinc-100 font-medium"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
          >
            Market
          </button>
          <button
            type="button"
            onClick={() => setOrderType("limit")}
            className={`px-2 py-0.5 rounded ${
              orderType === "limit"
                ? "bg-[#27272a] text-zinc-100 font-medium"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
          >
            Limit
          </button>
        </div>
      </div>

      {/* Input Fields */}
      <div className="space-y-3 font-mono text-xs">
        <div>
          <label className="block text-[11px] font-sans text-zinc-400 mb-1">
            Price ({orderType === "market" ? "Current" : "Limit"})
          </label>
          <input
            type="number"
            step="any"
            readOnly={orderType === "market"}
            value={entryPrice}
            onChange={(e) => setEntryPrice(e.target.value)}
            className={`w-full rounded border border-[#27272a] bg-[#141416] px-3 py-1.5 text-zinc-100 outline-none ${
              orderType === "limit" ? "focus:border-cyan-500" : "opacity-80"
            }`}
          />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-[11px] font-sans text-zinc-400 mb-1">
              Stop Loss
            </label>
            <input
              type="number"
              step="any"
              value={stopLoss}
              onChange={(e) => setStopLoss(e.target.value)}
              className="w-full rounded border border-[#27272a] bg-[#141416] px-3 py-1.5 text-zinc-100 outline-none focus:border-rose-500"
            />
          </div>
          <div>
            <label className="block text-[11px] font-sans text-zinc-400 mb-1">
              Take Profit
            </label>
            <input
              type="number"
              step="any"
              value={takeProfit}
              onChange={(e) => setTakeProfit(e.target.value)}
              className="w-full rounded border border-[#27272a] bg-[#141416] px-3 py-1.5 text-zinc-100 outline-none focus:border-emerald-500"
            />
          </div>
        </div>

        {/* Risk % Quick Select */}
        <div>
          <div className="flex justify-between text-[11px] font-sans text-zinc-400 mb-1">
            <span>Risk per Trade</span>
            <span className="text-zinc-200">{riskPct}%</span>
          </div>
          <div className="grid grid-cols-4 gap-1 font-sans">
            {[0.5, 1.0, 2.0, 3.0].map((pct) => (
              <button
                key={pct}
                type="button"
                onClick={() => setRiskPct(pct)}
                className={`py-1 text-xs rounded transition-colors ${
                  riskPct === pct
                    ? "bg-cyan-950/30 text-cyan-400 font-medium border border-cyan-500/40"
                    : "bg-[#141416] border border-[#27272a] text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {pct}%
              </button>
            ))}
          </div>
        </div>

        {/* Dynamic Position Calculations */}
        <div className="rounded border border-[#27272a] bg-[#141416] p-2.5 text-[11px] font-sans space-y-1">
          <div className="flex justify-between text-zinc-400">
            <span>Risk Amount:</span>
            <span className="font-mono text-zinc-200">${riskAmount.toFixed(2)}</span>
          </div>
          <div className="flex justify-between text-zinc-400">
            <span>Calculated Size:</span>
            <span className="font-mono text-zinc-200">{estimatedQty.toFixed(5)} units</span>
          </div>
          <div className="flex justify-between text-zinc-400">
            <span>Risk / Reward:</span>
            <span className="font-mono text-cyan-400">1 : {rrRatio}</span>
          </div>
        </div>
      </div>

      {/* Place Order CTA Button */}
      <button
        type="button"
        disabled={busy || !priceNum}
        onClick={() => void submitOrder()}
        className={`w-full py-2.5 rounded font-medium text-xs text-white transition-colors disabled:opacity-50 cursor-pointer ${
          side === "buy"
            ? "bg-emerald-600 hover:bg-emerald-500 shadow-md shadow-emerald-950/40"
            : "bg-rose-600 hover:bg-rose-500 shadow-md shadow-rose-950/40"
        }`}
      >
        {busy ? "Executing..." : `Submit ${side.toUpperCase()} Order (${paperMode ? "Paper" : "Live"})`}
      </button>

      {/* 1-Click Fast Execution Toggle */}
      <div className="flex items-center justify-between text-[11px] text-zinc-400 px-0.5 pt-0.5">
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={oneClickTrading}
            onChange={(e) => setOneClickTrading(e.target.checked)}
            className="rounded bg-[#1c1c20] border-[#27272a] text-cyan-500 focus:ring-0 cursor-pointer"
          />
          <span className="hover:text-zinc-200 transition-colors">1-Click Fast Execution</span>
        </label>
        <span className="text-[10px] text-zinc-500 font-mono">
          {oneClickTrading ? "Modal Bypassed" : "Modal Confirmation Active"}
        </span>
      </div>

      {/* Inline Feedback Banner */}
      {message && (
        <p
          className={`text-xs ${
            message.error ? "text-red-400" : "text-emerald-400"
          }`}
          role="status"
        >
          {message.text}
        </p>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// SUB-COMPONENT: STRATEGY STUDIO (CHARCOAL BLACK)
// ----------------------------------------------------------------------
function StrategyStudio({
  symbol,
  timeframe,
  candles,
  paperMode,
  onSaved,
}: {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  paperMode: boolean;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const [prompt, setPrompt] = useState(
    `Buy ${symbol} on ${timeframe} when RSI(14) < 30 and EMA(9) crosses above EMA(21) risk 0.5% stop loss 20 pips take profit 40 pips`
  );
  const [generatingAi, setGeneratingAi] = useState(false);
  const [aiExplanation, setAiExplanation] = useState<string | null>(null);
  const [aiModelUsed, setAiModelUsed] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [compiled, setCompiled] = useState<Record<string, unknown> | null>(null);
  const [showRawJson, setShowRawJson] = useState(false);
  const [busy, setBusy] = useState(false);
  const [accounts, setAccounts] = useState<
    { id: string; name: string; broker: string; enabled: boolean }[]
  >([]);
  const [accountId, setAccountId] = useState("");

  const presets = [
    {
      label: "Scalp Dips",
      prompt: `Scalping setup for ${symbol} on ${timeframe} buying oversold dips with tight 15-pip SL and 30-pip TP`,
    },
    {
      label: "Trend Follow",
      prompt: `Trend following strategy on ${symbol} ${timeframe} using moving average crossover with 1% risk`,
    },
    {
      label: "Mean Reversion",
      prompt: `Mean reversion strategy on ${symbol} on ${timeframe} with RSI and tight 20-pip risk`,
    },
  ];

  useEffect(() => {
    let live = true;
    void apiFetch("/broker-accounts", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return;
        const rows = await response.json();
        if (live) setAccounts(rows);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    setPrompt((value) =>
      value.replace(/^[A-Za-z]+\s+[^ ]+\s+on\s+[^ ]+/, `Buy ${symbol} on ${timeframe}`)
    );
  }, [symbol, timeframe]);

  async function post(path: string, body: unknown) {
    const response = await apiFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok)
      throw new Error(formatErrorDetail(payload.detail, `Request failed (${response.status})`));
    return payload;
  }

  async function generateWithAi(customIdea?: string) {
    const ideaToUse = (customIdea || prompt).trim();
    if (!ideaToUse) {
      toast.warning("Please type a trading idea or pick a preset", "Input Needed");
      return;
    }

    setGeneratingAi(true);
    setAiExplanation(null);
    try {
      const res = await post("/strategies/ai-generate", {
        user_prompt: ideaToUse,
        symbol,
        timeframe,
      });

      if (res.strategy_prompt) {
        setPrompt(res.strategy_prompt);
        setCompiled(res.rule);
        setAiExplanation(res.explanation || "Strategy generated successfully.");
        setAiModelUsed(`${res.provider.toUpperCase()} (${res.model})`);
        toast.success(
          `Synthesized rule for ${symbol} on ${timeframe}`,
          `AI Strategy Ready (${res.provider.toUpperCase()})`
        );
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : "AI Copilot generation failed";
      toast.error(msg, "AI Copilot Error");
    } finally {
      setGeneratingAi(false);
    }
  }

  async function compile() {
    setBusy(true);
    setMessage("");
    setCompiled(null);
    try {
      const result = await post("/strategies/compile", { prompt });
      setCompiled(result.rule);
      setMessage("Deterministic rule verified.");
      toast.success("Deterministic rule verified and compiled", "Compile Succeeded");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Compilation failed";
      setMessage(msg);
      toast.error(msg, "Compilation Error");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setMessage("");
    try {
      const result = await post("/strategies", {
        name: `${symbol} ${timeframe} strategy`,
        prompt,
        enabled: true,
        broker_account_id: accountId || null,
      });
      const saveMsg =
        result.mode === "live"
          ? "Strategy saved for protected broker execution."
          : "Strategy saved for paper runner.";
      setMessage(saveMsg);
      toast.success(saveMsg, "Strategy Saved");
      onSaved();
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Could not save strategy";
      setMessage(msg);
      toast.error(msg, "Save Failed");
    } finally {
      setBusy(false);
    }
  }

  async function backtest() {
    setBusy(true);
    setMessage("");
    try {
      const result = await post("/backtests", {
        prompt,
        candles: candles.map((bar) => ({
          timestamp: bar.time * 1000,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
          volume: bar.volume,
        })),
        starting_balance: 10_000,
      });
      const simMsg = `Backtest: ${result.trade_count} trades · ${result.win_rate_pct.toFixed(1)}% win · max DD ${result.max_drawdown_pct.toFixed(2)}%`;
      setMessage(simMsg);
      toast.success(simMsg, "Simulation Finished");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Backtest failed";
      setMessage(msg);
      toast.error(msg, "Backtest Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 font-sans">
      {/* Header bar: AI status & Venue selector */}
      <div className="flex items-center justify-between pb-2 border-b border-[#26262b]">
        <div className="flex items-center gap-1.5">
          <span className="text-cyan-400 text-sm">✨</span>
          <span className="text-xs font-semibold text-zinc-100">
            Strategy Studio
          </span>
          <span className="text-[10px] font-mono bg-cyan-950/40 border border-cyan-500/30 px-1.5 py-0.5 rounded text-cyan-400">
            Gemini AI
          </span>
        </div>
        <select
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
          className="rounded border border-[#27272a] bg-[#141416] px-2 py-0.5 text-[11px] text-zinc-300 outline-none focus:border-cyan-500 cursor-pointer max-w-[140px]"
        >
          <option value="">{paperMode ? "Paper Mode" : "Paper (Default)"}</option>
          {accounts
            .filter((a) => a.enabled)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.broker.toUpperCase()})
              </option>
            ))}
        </select>
      </div>

      {/* Preset Quick Chips */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-medium text-zinc-300">Strategy Rule Prompt</span>
          <span className="text-[10px] font-mono text-zinc-500">1-Click Presets:</span>
        </div>
        <div className="flex flex-wrap gap-1.5 mb-2">
          {presets.map((p) => (
            <button
              key={p.label}
              type="button"
              disabled={generatingAi}
              onClick={() => void generateWithAi(p.prompt)}
              className="text-[11px] font-medium rounded-md px-2.5 py-1 bg-[#141416] border border-[#27272a] text-zinc-300 hover:text-cyan-400 hover:border-cyan-500/50 hover:bg-cyan-950/20 transition-all cursor-pointer disabled:opacity-50"
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Single Unified Prompt Textarea */}
        <textarea
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value);
            setCompiled(null);
          }}
          rows={3}
          placeholder={`Describe your trading strategy (e.g. "Buy dips on ${symbol} when RSI < 30" or enter executable rule syntax)`}
          className="w-full resize-none rounded-lg border border-[#27272a] bg-[#141416] p-2.5 font-mono text-xs text-zinc-200 outline-none focus:border-cyan-500 leading-relaxed placeholder:font-sans placeholder:text-zinc-600"
        />
      </div>

      {/* AI Explanation / Succeeded banner */}
      {aiExplanation && (
        <div className="rounded border border-emerald-500/30 bg-emerald-950/25 p-2 text-[11px] text-emerald-300 space-y-0.5">
          <div className="flex items-center justify-between font-medium">
            <span>✓ Synthesized by AI</span>
            {aiModelUsed && <span className="text-[10px] font-mono text-emerald-400">{aiModelUsed}</span>}
          </div>
          <p className="text-zinc-300 text-[10.5px] leading-relaxed line-clamp-2">{aiExplanation}</p>
        </div>
      )}

      {/* Action Buttons: 2x2 Grid */}
      <div className="grid grid-cols-2 gap-1.5 pt-0.5">
        <button
          type="button"
          disabled={generatingAi || busy}
          onClick={() => void generateWithAi()}
          className="rounded bg-[#1f1f24] hover:bg-[#272730] border border-[#2b2b35] py-2 text-xs font-medium text-cyan-400 hover:text-cyan-300 transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5 cursor-pointer"
        >
          {generatingAi ? (
            <>
              <div className="w-3 h-3 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
              <span>Thinking...</span>
            </>
          ) : (
            <span>✨ Ask Gemini</span>
          )}
        </button>

        <button
          type="button"
          disabled={busy || generatingAi}
          onClick={() => void compile()}
          className="rounded border border-[#27272a] bg-[#18181b] hover:bg-[#222227] py-2 text-xs text-zinc-300 hover:text-white transition-colors disabled:opacity-50 cursor-pointer"
        >
          {busy ? "Checking..." : "Compile"}
        </button>

        <button
          type="button"
          disabled={busy || generatingAi}
          onClick={() => void save()}
          className="rounded bg-cyan-500 hover:bg-cyan-400 py-2 text-xs font-semibold text-zinc-950 transition-colors disabled:opacity-50 cursor-pointer shadow-sm shadow-cyan-950/40"
        >
          Save Strategy
        </button>

        <button
          type="button"
          disabled={busy || generatingAi || candles.length < 3}
          onClick={() => void backtest()}
          className="rounded border border-[#27272a] bg-[#18181b] hover:bg-[#222227] py-2 text-xs text-zinc-300 hover:text-white transition-colors disabled:opacity-50 cursor-pointer"
        >
          Simulate
        </button>
      </div>

      {message && (
        <p className="text-xs text-zinc-300" role="status">
          {message}
        </p>
      )}

      {/* Compiled Parameters Summary Chips */}
      {compiled && (
        <div className="rounded-lg border border-[#27272a] bg-[#141416] p-2.5 text-xs space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-emerald-400">
              ✓ Verified Rule Parameters
            </span>
            <button
              type="button"
              onClick={() => setShowRawJson(!showRawJson)}
              className="text-[10px] text-zinc-500 hover:text-zinc-300 font-mono underline cursor-pointer"
            >
              {showRawJson ? "Hide JSON" : "View JSON"}
            </button>
          </div>

          <div className="grid grid-cols-2 gap-1.5 font-mono text-[11px]">
            <div className="flex justify-between rounded bg-[#18181b] px-2 py-1 text-zinc-400">
              <span>Action:</span>
              <span className={`font-semibold ${String(compiled.side || "").toLowerCase() === "buy" ? "text-emerald-400" : "text-rose-400"}`}>
                {String(compiled.side || "BUY").toUpperCase()}
              </span>
            </div>
            <div className="flex justify-between rounded bg-[#18181b] px-2 py-1 text-zinc-400">
              <span>Market:</span>
              <span className="text-zinc-200">{String(compiled.symbol || symbol)}</span>
            </div>
            <div className="flex justify-between rounded bg-[#18181b] px-2 py-1 text-zinc-400">
              <span>Stop Loss:</span>
              <span className="text-rose-400">{String(compiled.stop_loss_pips || 20)} pips</span>
            </div>
            <div className="flex justify-between rounded bg-[#18181b] px-2 py-1 text-zinc-400">
              <span>Take Profit:</span>
              <span className="text-emerald-400">{String(compiled.take_profit_pips || 40)} pips</span>
            </div>
          </div>

          {showRawJson && (
            <pre className="max-h-28 overflow-auto rounded bg-[#0d0d10] p-2 font-mono text-[10px] text-emerald-300 border border-[#27272a]">
              {JSON.stringify(compiled, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
