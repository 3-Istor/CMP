import Image from "next/image";

import { cn } from "@/lib/utils";

/**
 * 3ISTOR SIGL brand mark — served as a self-contained SVG asset
 * (public/logo.svg) so it stays crisp at any size and needs no inline markup.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <Image
      src="/logo.svg"
      alt="3ISTOR SIGL logo"
      width={512}
      height={512}
      priority
      className={cn("h-9 w-9", className)}
    />
  );
}
