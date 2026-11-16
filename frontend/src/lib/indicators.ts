export type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };
export type LinePoint = { time: number; value: number };

export function ema(values: number[], period: number): number[] {
  const alpha = 2 / (period + 1);
  return values.reduce<number[]>((result, value, index) => {
    result.push(index === 0 ? value : value * alpha + result[index - 1] * (1 - alpha));
    return result;
  }, []);
}

export function sma(values: number[], period: number): Array<number | null> {
  return values.map((_, index) => index + 1 < period ? null :
    values.slice(index + 1 - period, index + 1).reduce((sum, value) => sum + value, 0) / period);
}

export function rsi(values: number[], period = 14): Array<number | null> {
  return values.map((_, index) => {
    if (index < period) return null;
    let gains = 0;
    let losses = 0;
    for (let i = index - period + 1; i <= index; i += 1) {
      const change = values[i] - values[i - 1];
      if (change > 0) gains += change;
      else losses -= change;
    }
    if (losses === 0) return gains > 0 ? 100 : 50;
    const relativeStrength = gains / losses;
    return 100 - 100 / (1 + relativeStrength);
  });
}

export function macd(values: number[], fast = 12, slow = 26, signalPeriod = 9) {
  const fastLine = ema(values, fast);
  const slowLine = ema(values, slow);
  const line = values.map((_, index) => fastLine[index] - slowLine[index]);
  const signal = ema(line, signalPeriod);
  return { line, signal, histogram: line.map((value, index) => value - signal[index]) };
}

export function points(candles: Candle[], values: Array<number | null>): LinePoint[] {
  return candles.flatMap((candle, index) => values[index] === null || !Number.isFinite(values[index])
    ? [] : [{ time: candle.time, value: values[index] as number }]);
}
