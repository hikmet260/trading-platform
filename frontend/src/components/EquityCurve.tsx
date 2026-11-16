export default function EquityCurve({ values, height = 180 }: { values: number[]; height?: number }) {
  const width = 720;
  if (!values.length) return <div className="grid h-44 place-items-center text-sm text-slate-500">No closed trade history yet.</div>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  const points = values.map((value, index) => `${(index / Math.max(values.length - 1, 1)) * width},${height - 10 - ((value - min) / spread) * (height - 20)}`).join(" ");
  return <div className="overflow-hidden"><svg viewBox={`0 0 ${width} ${height}`} className="h-44 w-full" role="img" aria-label="Cumulative equity curve"><defs><linearGradient id="equity-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#22d3ee" stopOpacity=".24"/><stop offset="100%" stopColor="#22d3ee" stopOpacity="0"/></linearGradient></defs><polyline points={`0,${height} ${points} ${width},${height}`} fill="url(#equity-fill)" stroke="none"/><polyline points={points} fill="none" stroke="#22d3ee" strokeWidth="2" vectorEffect="non-scaling-stroke"/></svg><div className="flex justify-between font-mono text-xs text-slate-500"><span>{min.toFixed(2)}</span><span>{max.toFixed(2)}</span></div></div>;
}
