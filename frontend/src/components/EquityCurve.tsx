export default function EquityCurve({ values, height = 180 }: { values: number[]; height?: number }) {
  const width = 720;
  if (!values.length) return <div className="grid h-44 place-items-center text-sm text-zinc-500">No closed trade history yet.</div>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  const points = values.map((value, index) => `${(index / Math.max(values.length - 1, 1)) * width},${height - 10 - ((value - min) / spread) * (height - 20)}`).join(" ");
  return (
    <div className="overflow-hidden">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-44 w-full" role="img" aria-label="Cumulative equity curve">
        <polyline points={points} fill="none" stroke="#3b82f6" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between font-mono text-xs text-zinc-500">
        <span>${min.toFixed(2)}</span>
        <span>${max.toFixed(2)}</span>
      </div>
    </div>
  );
}
