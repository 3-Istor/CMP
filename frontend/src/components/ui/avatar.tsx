import * as React from "react";

import { cn } from "@/lib/utils";

interface AvatarProps extends React.ComponentProps<"div"> {
  src?: string | null;
  alt?: string;
  fallback?: string;
}

function Avatar({ className, src, alt, fallback, ...props }: AvatarProps) {
  const [imageError, setImageError] = React.useState(false);

  const initials = React.useMemo(() => {
    if (fallback) {
      return fallback
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);
    }
    return "?";
  }, [fallback]);

  return (
    <div
      className={cn(
        "relative flex h-10 w-10 shrink-0 overflow-hidden rounded-full",
        className,
      )}
      {...props}
    >
      {src && !imageError ? (
        <img
          src={src}
          alt={alt || "Avatar"}
          className="aspect-square h-full w-full object-cover"
          onError={() => setImageError(true)}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-muted text-sm font-medium">
          {initials}
        </div>
      )}
    </div>
  );
}

export { Avatar };
