"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
} from "lightweight-charts";
import { ema, macd, points, rsi, sma, type Candle } from "@/lib/indicators";
import Link from "next/link";
import { API, apiFetch } from "@/lib/api";

type DataSeries = { setData(data: never[]): void };

const intervals: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };

export default function TradingDashboard() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("1m");
  const [exchange, setExchange] = useState("binance");
  const [status, setStatus] = useState("Connecting");
  const [latest, setLatest] = useState<number | null>(null);
  const [bars, setBars] = useState<Candle[]>([]);
  const [system, setSystem] = useState<{ kill_switch?: boolean; daily_drawdown_halted?: boolean; execution_mode?: string; live_order_api_enabled?: boolean; paper_mode?: boolean; engine_enabled?: boolean; engine_running?: boolean } | null>(null);
  const [riskLimit, setRiskLimit] = useState("3");
  const [riskMessage, setRiskMessage] = useState("");
  const priceChart = useRef<HTMLDivElement>(null);
  const rsiChart = useRef<HTMLDivElement>(null);
  const macdChart = useRef<HTMLDivElement>(null);
  const chartApis = useRef<IChartApi[]>([]);
  const seriesApis = useRef<DataSeries[]>([]);
  const didFitContent = useRef(false);
  const lastTrade = bars[bars.length - 1];

  useEffect(() => {
    let live = true;
    const refresh = async () => {
      try {
        const [stateResponse, riskResponse] = await Promise.all([apiFetch("/system/status", { cache: "no-store" }), apiFetch("/risk/settings", { cache: "no-store" })]);
        if (!stateResponse.ok || !riskResponse.ok) return;
        const [state, risk] = await Promise.all([stateResponse.json(), riskResponse.json()]);
        if (live) { setSystem(state); setRiskLimit(String(risk.max_daily_drawdown_pct)); }
      } catch { /* Status remains visibly unavailable if the API is offline. */ }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    return () => { live = false; clearInterval(timer); };
  }, []);

  async function toggleKillSwitch() {
    const activate = !system?.kill_switch;
    const response = await apiFetch("/system/kill-switch", { method: activate ? "POST" : "DELETE" });
    if (!response.ok) { setRiskMessage("Could not update the emergency switch."); return; }
    const payload = await response.json();
    setSystem((previous) => ({ ...(previous ?? {}), kill_switch: activate }));
    setRiskMessage(activate
      ? `Runner paused. Closed ${payload.closed_paper_positions ?? 0} paper and ${payload.closed_live_positions ?? 0} live position(s); ${payload.unclosed_live_positions ?? 0} live close(s) need attention.`
      : `Runner resumed in ${system?.paper_mode === false ? "live" : "paper"} mode.`);
  }

  async function saveRiskLimit() {
    const response = await apiFetch("/risk/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ max_daily_drawdown_pct: Number(riskLimit), news_blackout_minutes: 15 }) });
    setRiskMessage(response.ok ? "Risk guardrails saved." : "Risk limit was rejected by the API.");
  }

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
      upper: points(bars, middle.map((value, index) => value === null || deviations[index] === null ? null : value + 2 * (deviations[index] as number))),
      lower: points(bars, middle.map((value, index) => value === null || deviations[index] === null ? null : value - 2 * (deviations[index] as number))),
      rsi: points(bars, rsi(closes)),
      macd: macd(closes),
    };
  }, [bars]);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let currentBar: Candle | undefined;

    async function connect() {
      setStatus("Loading market data");
      try {
        const query = new URLSearchParams({ symbol, timeframe, limit: "500", exchange });
        const endpoint = exchange === "mt5"
          ? `/market/mt5/${encodeURIComponent(symbol)}/candles?${query.toString()}`
          : `/market/crypto/candles?${query.toString()}`;
        const response = await apiFetch(endpoint, { cache: "no-store" });
        if (!response.ok) throw new Error(`Market data returned ${response.status}`);
        const payload = await response.json();
        const initial: Candle[] = payload.candles.map((bar: { timestamp: number; open: number; high: number; low: number; close: number; volume: number }) => ({
          time: Math.floor(bar.timestamp / 1000), open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume,
        }));
        if (stopped) return;
        setBars(initial);
        setStatus("Opening WebSocket");
      } catch (error) {
        if (!stopped) setStatus(error instanceof Error ? error.message : "Market data unavailable");
      }

      if (exchange === "mt5") connectMt5Polling();
      else connectStream();
    }

    function acceptTick(tick: { last?: number; bid?: number; ask?: number; timestamp?: number }) {
      const price = tick.last && tick.last > 0 ? tick.last : (tick.bid && tick.ask ? (tick.bid + tick.ask) / 2 : undefined);
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
          currentBar = { ...last, high: Math.max(last.high, price), low: Math.min(last.low, price), close: price };
          next[next.length - 1] = currentBar;
        } else {
          currentBar = { time, open: last.close, high: price, low: price, close: price, volume: 0 };
          next.push(currentBar);
        }
        return next.slice(-1000);
      });
    }

    function connectMt5Polling() {
      if (stopped) return;
      setStatus("Polling MT5 terminal");
      const poll = async () => {
        try {
          const response = await apiFetch(`/market/mt5/${encodeURIComponent(symbol)}/ticker`, { cache: "no-store" });
          if (!response.ok) throw new Error(`MT5 quote returned ${response.status}`);
          acceptTick(await response.json());
          if (!stopped) setStatus("MT5 live");
        } catch (error) { if (!stopped) setStatus(error instanceof Error ? error.message : "MT5 feed unavailable"); }
      };
      void poll();
      reconnectTimer = setInterval(() => void poll(), 1000);
    }

    function connectStream() {
      if (stopped) return;
      const wsUrl = new URL(API || window.location.origin);
      wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
      wsUrl.pathname = "/ws/crypto/ticker";
      wsUrl.search = new URLSearchParams({ exchange, symbol }).toString();
      socket = new WebSocket(wsUrl);
      socket.onopen = () => { if (!stopped) setStatus("Live"); };
      socket.onerror = () => { if (!stopped) setStatus("Stream reconnecting"); };
      socket.onclose = () => {
        if (!stopped) reconnectTimer = setTimeout(connectStream, 2500);
      };
      socket.onmessage = (event) => {
        try {
          const tick = JSON.parse(event.data) as { last?: number; timestamp?: number };
          acceptTick(tick);
        } catch { setStatus("Invalid market data frame"); }
      };
    }

    void connect();
    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [symbol, timeframe, exchange]);

  useEffect(() => {
    const refs = [priceChart.current, rsiChart.current, macdChart.current];
    if (refs.some((element) => !element)) return;
    const colors = { background: "#0b111a", text: "#a9b5c7", grid: "#1e2937" };
    const create = (element: HTMLDivElement, height: number) => createChart(element, {
      width: element.clientWidth, height,
      layout: { background: { color: colors.background }, textColor: colors.text },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.grid }, timeScale: { borderColor: colors.grid, timeVisible: true },
      crosshair: { vertLine: { color: "#4b6078" }, horzLine: { color: "#4b6078" } },
    });
    const price = create(priceChart.current as HTMLDivElement, 400);
    const candles = price.addSeries(CandlestickSeries, { upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444" });
    const dataSeries: DataSeries[] = [candles as unknown as DataSeries];
    const addLine = (chart: IChartApi, color: string) => {
      const line = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      dataSeries.push(line as unknown as DataSeries);
    };
    addLine(price, "#f59e0b");
    addLine(price, "#38bdf8");
    addLine(price, "#a78bfa");
    addLine(price, "#64748b");
    addLine(price, "#64748b");

    const rsiApi = create(rsiChart.current as HTMLDivElement, 120);
    addLine(rsiApi, "#c084fc");
    const macdApi = create(macdChart.current as HTMLDivElement, 140);
    addLine(macdApi, "#38bdf8");
    addLine(macdApi, "#f97316");
    const histogram = macdApi.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false });
    dataSeries.push(histogram as unknown as DataSeries);

    const charts = [price, rsiApi, macdApi];
    chartApis.current = charts;
    seriesApis.current = dataSeries;
    const observer = new ResizeObserver(() => charts.forEach((chart, index) => chart.applyOptions({ width: refs[index]?.clientWidth ?? 0 })));
    refs.forEach((element) => element && observer.observe(element));
    return () => { observer.disconnect(); charts.forEach((chart) => chart.remove()); chartApis.current = []; seriesApis.current = []; didFitContent.current = false; };
  }, []);

  useEffect(() => {
    if (!bars.length || seriesApis.current.length < 10) return;
    const timeData = (pointsList: { time: number; value: number }[]) => pointsList.map((point) => ({ time: point.time as never, value: point.value }));
    seriesApis.current[0].setData(bars.map(({ time, open, high, low, close }) => ({ time: time as never, open, high, low, close })) as never[]);
    seriesApis.current[1].setData(timeData(computed.ema9) as never[]);
    seriesApis.current[2].setData(timeData(computed.ema21) as never[]);
    seriesApis.current[3].setData(timeData(computed.sma20) as never[]);
    seriesApis.current[4].setData(timeData(computed.upper) as never[]);
    seriesApis.current[5].setData(timeData(computed.lower) as never[]);
    seriesApis.current[6].setData(timeData(computed.rsi) as never[]);
    seriesApis.current[7].setData(timeData(points(bars, computed.macd.line)) as never[]);
    seriesApis.current[8].setData(timeData(points(bars, computed.macd.signal)) as never[]);
    seriesApis.current[9].setData(bars.map((bar, index) => ({ time: bar.time as never, value: computed.macd.histogram[index], color: computed.macd.histogram[index] >= 0 ? "#166534" : "#991b1b" })) as never[]);
    if (!didFitContent.current) {
      chartApis.current.forEach((chart) => chart.timeScale().fitContent());
      didFitContent.current = true;
    }
  }, [bars, computed]);

  return (
    <main className="mx-auto min-h-screen max-w-[1500px] px-5 py-6 md:px-8">
      <header className="flex flex-wrap items-center justify-between gap-5 border-b border-slate-800 pb-5">
        <div><p className="text-xs uppercase tracking-[0.25em] text-cyan-400">Institutional market terminal</p><h1 className="mt-1 text-2xl font-semibold">Market workspace</h1></div>
        <div className="flex flex-wrap items-center gap-3">
          <nav className="flex gap-3 text-xs text-slate-400"><Link href="/strategies" className="hover:text-cyan-300">Strategies</Link><Link href="/accounts" className="hover:text-cyan-300">Accounts</Link><Link href="/backtest" className="hover:text-cyan-300">Backtest</Link><Link href="/trades" className="hover:text-cyan-300">Trades</Link></nav>
          <button onClick={() => void toggleKillSwitch()} className={`rounded-lg px-3 py-2 text-xs font-semibold ${system?.kill_switch ? "bg-emerald-700 text-white" : "bg-red-700 text-white"}`}>{system?.kill_switch ? "Resume runner" : "Kill switch"}</button>
          <select aria-label="Broker / exchange" value={exchange} onChange={(event) => { const next = event.target.value; setExchange(next); if (next === "mt5") setSymbol("XAUUSD"); else if (symbol === "XAUUSD" || symbol === "EURUSD") setSymbol("BTC/USDT"); }} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
            <option value="binance">Binance</option><option value="bybit">Bybit</option><option value="okx">OKX</option><option value="mt5">MetaTrader 5</option>
          </select>
          <input aria-label="Symbol" value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} className="w-32 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm" />
          <select aria-label="Timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
            {Object.keys(intervals).map((value) => <option key={value}>{value}</option>)}
          </select>
          <span className={`rounded-full px-3 py-1 text-xs ${status === "Live" ? "bg-emerald-950 text-emerald-300" : "bg-amber-950 text-amber-300"}`}>{status}</span>
        </div>
      </header>

      <section className="mt-4 grid gap-3 md:grid-cols-[1fr_auto]">
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs uppercase tracking-widest text-cyan-400">System status</p><p className="mt-1 text-sm">{system?.kill_switch ? "HALTED · kill switch active" : system?.daily_drawdown_halted ? "HALTED · daily drawdown limit reached" : !system?.engine_enabled ? "STOPPED · engine disabled by operator" : `RUNNING · ${system?.execution_mode ?? "paper"} · ${system?.paper_mode === false ? "protected live strategy runner" : "paper strategy runner"}`}</p></div><span className="text-xs text-slate-500">{system?.live_order_api_enabled ? "Protected live orders enabled" : "Live execution disabled"}</span></div>
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <label htmlFor="drawdown-limit" className="text-xs text-slate-400">Daily loss cap %</label><input id="drawdown-limit" type="number" min="0.1" max="100" step="0.1" value={riskLimit} onChange={(event) => setRiskLimit(event.target.value)} className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"/><button onClick={() => void saveRiskLimit()} className="rounded bg-slate-700 px-3 py-1.5 text-xs">Save</button>
        </div>
      </section>
      {riskMessage && <p className="mt-2 text-xs text-cyan-300" role="status">{riskMessage}</p>}

      <section className="mt-5 grid gap-3 sm:grid-cols-4">
        <Stat label="Last price" value={latest?.toLocaleString(undefined, { maximumFractionDigits: 6 }) ?? "—"} />
        <Stat label="RSI (14)" value={computed.rsi.at(-1)?.value.toFixed(2) ?? "—"} />
        <Stat label="MACD (12, 26, 9)" value={computed.macd.line.at(-1)?.toFixed(4) ?? "—"} />
        <Stat label="Current bar" value={lastTrade ? new Date(lastTrade.time * 1000).toLocaleTimeString() : "—"} />
      </section>

      <section className="mt-5 rounded-xl border border-slate-800 bg-[#0b111a] p-3 md:p-5">
        <div className="mb-3 flex flex-wrap gap-4 text-xs text-slate-400"><span>EMA 9 <b className="text-amber-400">●</b></span><span>EMA 21 <b className="text-sky-400">●</b></span><span>SMA 20 <b className="text-violet-400">●</b></span><span>Bollinger 20, 2σ <b className="text-slate-500">●</b></span></div>
        <div ref={priceChart} className="w-full" />
        <p className="mb-1 mt-4 text-xs text-slate-400">RSI (14)</p><div ref={rsiChart} className="w-full" />
        <p className="mb-1 mt-4 text-xs text-slate-400">MACD (12, 26, 9)</p><div ref={macdChart} className="w-full" />
      </section>
      <StrategyStudio symbol={symbol} timeframe={timeframe} candles={bars} paperMode={system?.paper_mode !== false} />
      <footer className="mt-4 text-xs text-slate-500">{system?.paper_mode === false ? "Live strategy mode · protected broker execution" : "Paper mode"} · Public exchange market data · <a className="underline" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a></footer>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4"><p className="text-xs text-slate-500">{label}</p><p className="mt-2 font-mono text-lg text-slate-100">{value}</p></div>;
}

function StrategyStudio({ symbol, timeframe, candles, paperMode }: { symbol: string; timeframe: string; candles: Candle[]; paperMode: boolean }) {
  const [prompt, setPrompt] = useState(`Buy ${symbol} on ${timeframe} when RSI(14) < 30 and EMA(9) crosses above EMA(21) risk 0.5% stop loss 20 pips take profit 40 pips`);
  const [message, setMessage] = useState("");
  const [compiled, setCompiled] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [accounts, setAccounts] = useState<{ id: string; name: string; broker: string; enabled: boolean }[]>([]);
  const [accountId, setAccountId] = useState("");
  useEffect(() => {
    let live = true;
    void apiFetch("/broker-accounts", { cache: "no-store" }).then(async (response) => {
      if (!response.ok) return;
      const rows = await response.json();
      if (live) setAccounts(rows);
    }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  useEffect(() => {
    setPrompt((value) => value.replace(/^[A-Za-z]+\s+[^ ]+\s+on\s+[^ ]+/, `Buy ${symbol} on ${timeframe}`));
  }, [symbol, timeframe]);

  async function post(path: string, body: unknown) {
    const response = await apiFetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(Array.isArray(payload.detail) ? payload.detail.map((item: { msg?: string }) => item.msg).join(", ") : payload.detail ?? `Request failed (${response.status})`);
    return payload;
  }

  async function compile() {
    setBusy(true); setMessage(""); setCompiled(null);
    try { const result = await post("/strategies/compile", { prompt }); setCompiled(result.rule); setMessage("Compiled into a strict deterministic rule."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Compilation failed"); }
    finally { setBusy(false); }
  }

  async function save() {
    setBusy(true); setMessage("");
    try { const result = await post("/strategies", { name: `${symbol} ${timeframe} strategy`, prompt, enabled: true, broker_account_id: accountId || null }); setMessage(result.mode === "live" ? "Strategy saved for protected broker execution." : "Strategy saved and enabled for paper evaluation."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Could not save strategy"); }
    finally { setBusy(false); }
  }

  async function backtest() {
    setBusy(true); setMessage("");
    try {
      const result = await post("/backtests", {
        prompt,
        candles: candles.map((bar) => ({ timestamp: bar.time * 1000, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume })),
        starting_balance: 10_000,
      });
      setMessage(`Backtest · ${result.trade_count} trades · ${result.win_rate_pct.toFixed(1)}% win rate · profit factor ${result.profit_factor ?? "—"} · max drawdown ${result.max_drawdown_pct.toFixed(2)}%`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Backtest failed"); }
    finally { setBusy(false); }
  }

  return (
    <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/50 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="text-xs uppercase tracking-[0.2em] text-cyan-400">Strategy studio</p><h2 className="mt-1 text-lg font-semibold">Deterministic rule compiler</h2></div>
        <span className="rounded-full bg-amber-950 px-3 py-1 text-xs text-amber-300">{paperMode ? "PAPER MODE" : accountId ? "LIVE BROKER ACCOUNT" : "LIVE ACCOUNT REQUIRED"}</span>
      </div>
      <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={3} className="mt-4 w-full resize-y rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-sm text-slate-200 outline-none focus:border-cyan-700" aria-label="Strategy prompt" />
      <label className="mt-3 block max-w-xl text-xs text-slate-400">Execution account (required when live strategy mode is enabled)<select value={accountId} onChange={(event) => setAccountId(event.target.value)} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm"><option value="">Paper / no account</option>{accounts.filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.broker}</option>)}</select></label>
      <div className="mt-3 flex flex-wrap gap-2">
        <button disabled={busy} onClick={() => void compile()} className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">Compile</button>
        <button disabled={busy} onClick={() => void save()} className="rounded-lg border border-slate-600 px-4 py-2 text-sm text-slate-200 disabled:opacity-50">{paperMode ? "Save and enable paper strategy" : "Save and enable live strategy"}</button>
        <button disabled={busy || candles.length < 3} onClick={() => void backtest()} className="rounded-lg border border-slate-600 px-4 py-2 text-sm text-slate-200 disabled:opacity-50">Backtest loaded candles</button>
      </div>
      {message && <p className="mt-3 text-sm text-slate-300" role="status">{message}</p>}
      {compiled && <pre className="mt-3 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-emerald-300">{JSON.stringify(compiled, null, 2)}</pre>}
      <p className="mt-3 text-xs text-slate-500">Supported form: BUY SYMBOL on TIMEFRAME when RSI(14) &lt; 30 and EMA(9) crosses above EMA(21) risk 0.5% stop loss 20 pips take profit 40 pips. Unknown terms are rejected.</p>
    </section>
  );
}
