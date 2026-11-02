import type { MetricPoint } from "@/types";

const WIDTH = 120;
const HEIGHT = 36;

export function Sparkline({
  points,
  label,
}: {
  points: MetricPoint[];
  label: string;
}) {
  if (points.length < 2) {
    return <div className="h-9" aria-hidden />;
  }
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const path = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * WIDTH;
      const y = HEIGHT - 2 - ((p.value - min) / span) * (HEIGHT - 4);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      className="h-9 w-full text-primary"
    >
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
