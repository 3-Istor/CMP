import {
  Cloud,
  CloudLightning,
  Database,
  Globe,
  Package,
  Rocket,
  Server,
  Ship,
  type LucideIcon,
} from "lucide-react";

// Template manifests (app-templates repo) still carry emojis; they are mapped here so none reach the UI.
const ICONS: Record<string, LucideIcon> = {
  rocket: Rocket,
  "\u{1F680}": Rocket,
  cloud: Cloud,
  "\u{2601}\u{FE0F}": Cloud,
  "cloud-lightning": CloudLightning,
  "\u{1F329}\u{FE0F}": CloudLightning,
  globe: Globe,
  "\u{1F310}": Globe,
  database: Database,
  "\u{1F418}": Database,
  kubernetes: Ship,
  "\u{2638}\u{FE0F}": Ship,
  server: Server,
};

export function TemplateIcon({
  icon,
  className = "h-8 w-8",
}: {
  icon?: string | null;
  className?: string;
}) {
  const Icon = (icon && ICONS[icon.trim()]) || Package;
  return <Icon className={`${className} text-primary`} aria-hidden="true" />;
}
