import { CLOUD_META } from "@/components/finops/format";
import { cn } from "@/lib/utils";

export type GlyphCloud = "onprem" | "aws" | "gcp" | "public";

const LABEL: Record<GlyphCloud, string> = { onprem: "K3s", aws: "AWS", gcp: "GCP", public: "PUB" };
const SIZE = { sm: "size-6", md: "size-9", lg: "size-16" } as const;
const FONT = { sm: 13, md: 11, lg: 10 } as const;

export type Tone = "up" | "degraded" | "down" | "neutral";

/** Cluster state folded into the four tones the UI draws. */
export const toneOf = (state: string | undefined): Tone =>
  state === "up" || state === "degraded" ? state : state === "down" || state === "unreachable" ? "down" : "neutral";

const RING: Record<Tone, string> = {
  up: "border-emerald-500",
  degraded: "border-amber-500",
  down: "border-destructive border-dashed",
  neutral: "border-muted-foreground/50",
};

/** Hexagon in the cloud's colour with a state ring (up green, degraded amber, down red dashed). */
export function ClusterGlyph({
  cloud,
  state,
  size = "md",
  className,
}: {
  cloud: GlyphCloud;
  state?: string;
  size?: keyof typeof SIZE;
  className?: string;
}) {
  // Slightly darkened fill so the white label keeps its contrast on orange.
  const fill = cloud === "public" ? "var(--muted-foreground)" : `color-mix(in oklab, ${CLOUD_META[cloud].color} 80%, black)`;
  return (
    <span aria-hidden data-cloud={cloud} data-state={state} className={cn("relative inline-grid shrink-0 place-items-center", SIZE[size], className)}>
      <svg viewBox="0 0 40 40" className="size-full">
        <path d="M20 2l15.6 9v18L20 38 4.4 29V11z" fill={fill} />
        <text x="20" y="24.5" textAnchor="middle" fontSize={FONT[size]} fontWeight={600} fill="#fff" className="font-heading">
          {LABEL[cloud]}
        </text>
      </svg>
      {state !== undefined && <span className={cn("absolute -inset-1 rounded-full border-2", RING[toneOf(state)])} />}
    </span>
  );
}
