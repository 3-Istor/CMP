import {
  BarChart3,
  Calendar,
  Cpu,
  Database,
  Gamepad2,
  Globe,
  Leaf,
  Lock,
  Mail,
  MessageCircle,
  Package,
  ShoppingCart,
  type LucideIcon,
} from "lucide-react";

export const BUILTIN_ICONS = {
  MessageCircle,
  Globe,
  Database,
  ShoppingCart,
  BarChart3,
  Cpu,
  Mail,
  Calendar,
  Lock,
  Gamepad2,
  Leaf,
  Package,
} satisfies Record<string, LucideIcon>;

export type BuiltinIconName = keyof typeof BUILTIN_ICONS;

export const BUILTIN_ICON_NAMES = Object.keys(
  BUILTIN_ICONS,
) as BuiltinIconName[];

// Shades picked for at least 3:1 against white (light) and the dark surface (dark).
export const COLOR_TEXT = {
  indigo: "text-indigo-600 dark:text-indigo-400",
  emerald: "text-emerald-700 dark:text-emerald-400",
  amber: "text-amber-700 dark:text-amber-400",
  rose: "text-rose-600 dark:text-rose-400",
  sky: "text-sky-700 dark:text-sky-400",
  slate: "text-slate-600 dark:text-slate-400",
} as const;

export const COLOR_SWATCH = {
  indigo: "bg-indigo-600 dark:bg-indigo-400",
  emerald: "bg-emerald-700 dark:bg-emerald-400",
  amber: "bg-amber-700 dark:bg-amber-400",
  rose: "bg-rose-600 dark:bg-rose-400",
  sky: "bg-sky-700 dark:bg-sky-400",
  slate: "bg-slate-600 dark:bg-slate-400",
} as const;

export type IconColor = keyof typeof COLOR_TEXT;

export const ICON_COLORS = Object.keys(COLOR_TEXT) as IconColor[];

export const MAX_UPLOAD_BYTES = 512 * 1024;
export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];
