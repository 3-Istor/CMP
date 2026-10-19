import { cn } from "@/lib/utils";

export type MascotMood = "idle" | "working" | "happy";

/**
 * Nimbus, the platform's helper: a small cloud that shows what the assistant
 * is doing. Drawn inline so it follows the theme's primary color.
 */
export function Mascot({
  mood = "idle",
  className,
}: {
  mood?: MascotMood;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 120 96"
      role="img"
      aria-label={
        mood === "working"
          ? "Nimbus is working"
          : mood === "happy"
            ? "Nimbus is happy"
            : "Nimbus"
      }
      className={cn(
        "text-primary motion-safe:animate-[nimbus-float_4s_ease-in-out_infinite]",
        className,
      )}
    >
      <defs>
        <linearGradient id="nimbus-body" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="currentColor" stopOpacity="0.95" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0.7" />
        </linearGradient>
      </defs>
      {/* antenna: sparks while working */}
      <line
        x1="60"
        y1="20"
        x2="60"
        y2="8"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <circle
        cx="60"
        cy="6"
        r="5"
        fill="currentColor"
        className={cn(
          mood === "working" &&
            "motion-safe:animate-[nimbus-spark_0.9s_ease-in-out_infinite]",
        )}
      />
      <path
        d="M30 80h62a20 20 0 0 0 2-39.9A28 28 0 0 0 40 34a22 22 0 0 0-10 46z"
        fill="url(#nimbus-body)"
      />
      {/* face */}
      <g fill="var(--background)">
        {mood === "happy" ? (
          <>
            <path
              d="M44 54q5-6 10 0"
              stroke="var(--background)"
              strokeWidth="3.5"
              fill="none"
              strokeLinecap="round"
            />
            <path
              d="M66 54q5-6 10 0"
              stroke="var(--background)"
              strokeWidth="3.5"
              fill="none"
              strokeLinecap="round"
            />
          </>
        ) : (
          <g className="motion-safe:animate-[nimbus-blink_5s_infinite] [transform-box:fill-box] [transform-origin:center]">
            <ellipse cx="49" cy="53" rx="4.5" ry="5.5" />
            <ellipse cx="71" cy="53" rx="4.5" ry="5.5" />
          </g>
        )}
        <path
          d={mood === "working" ? "M54 66h12" : "M53 64q7 6 14 0"}
          stroke="var(--background)"
          strokeWidth="3"
          fill="none"
          strokeLinecap="round"
        />
      </g>
    </svg>
  );
}
