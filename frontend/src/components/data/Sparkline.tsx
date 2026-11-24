import { cn } from "@/lib/utils";

const WIDTH = 120;
const HEIGHT = 36;

export interface SparklineProps {
  /** Oldest first; a null is a gap in the line. */
  values: (number | null)[];
  /** The accessible name: what the line shows and its last value. */
  label: string;
  /** Any CSS colour; defaults to the primary colour. */
  color?: string;
  /** Sizing classes; defaults to h-9 w-full. */
  className?: string;
  loading?: boolean;
}

export function Sparkline({
  values,
  label,
  color,
  className,
  loading = false,
}: SparklineProps) {
  const size = className ?? "h-9 w-full";
  const known = values.filter((v): v is number => v !== null);
  if (loading) {
    return (
      <div className={cn("animate-pulse rounded bg-muted", size)} aria-hidden />
    );
  }
  if (known.length < 2) return <div className={size} aria-hidden />;
  const min = Math.min(...known);
  const max = Math.max(...known);
  const span = max - min || 1;
  let path = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    const x = (i / (values.length - 1)) * WIDTH;
    const y = HEIGHT - 2 - ((v - min) / span) * (HEIGHT - 4);
    path += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)} `;
    pen = true;
  });
  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      className={cn("text-primary", size)}
      style={color ? { color } : undefined}
    >
      <path
        d={path.trim()}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
