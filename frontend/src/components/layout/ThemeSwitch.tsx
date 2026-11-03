"use client";

import { useTheme, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef } from "react";

const OPTIONS = [
  { value: "light", icon: Sun },
  { value: "dark", icon: Moon },
  { value: "system", icon: Monitor },
] as const satisfies readonly { value: Theme; icon: unknown }[];

export function ThemeSwitch({ vertical = false }: { vertical?: boolean }) {
  const t = useTranslations("shell.theme");
  const [theme, choose] = useTheme();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    const step =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (step === 0) return;
    event.preventDefault();
    const next = (index + step + OPTIONS.length) % OPTIONS.length;
    choose(OPTIONS[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={t("label")}
      className={cn(
        "inline-flex gap-0.5 rounded-md border bg-background p-0.5",
        vertical && "flex-col",
      )}
    >
      {OPTIONS.map(({ value, icon: Icon }, index) => {
        const checked = theme === value;
        return (
          <button
            key={value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={t(value)}
            title={t(value)}
            tabIndex={checked ? 0 : -1}
            onClick={() => choose(value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-sm transition-colors",
              checked
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" aria-hidden />
          </button>
        );
      })}
    </div>
  );
}
