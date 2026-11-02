import { cn } from "@/lib/utils";
import * as React from "react";

interface DropdownMenuProps {
  children: React.ReactNode;
  className?: string;
}

interface DropdownMenuTriggerProps {
  children: React.ReactNode;
  asChild?: boolean;
}

interface DropdownMenuContentProps {
  children: React.ReactNode;
  align?: "start" | "center" | "end";
  side?: "bottom" | "top";
  className?: string;
}

interface DropdownMenuItemProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  children: React.ReactNode;
  asChild?: boolean;
}

const DropdownMenuContext = React.createContext<{
  open: boolean;
  setOpen: (open: boolean) => void;
}>({
  open: false,
  setOpen: () => {},
});

function DropdownMenu({ children, className }: DropdownMenuProps) {
  const [open, setOpen] = React.useState(false);

  return (
    <DropdownMenuContext.Provider value={{ open, setOpen }}>
      <div className={cn("relative inline-block", className)}>{children}</div>
    </DropdownMenuContext.Provider>
  );
}

function DropdownMenuTrigger({ children, asChild }: DropdownMenuTriggerProps) {
  const { open, setOpen } = React.useContext(DropdownMenuContext);

  const handleClick = () => setOpen(!open);

  if (asChild && React.isValidElement(children)) {
    return React.cloneElement(
      children as React.ReactElement<Record<string, unknown>>,
      {
        onClick: handleClick,
        "aria-haspopup": "menu",
        "aria-expanded": open,
      },
    );
  }

  return (
    <button
      onClick={handleClick}
      type="button"
      aria-haspopup="menu"
      aria-expanded={open}
    >
      {children}
    </button>
  );
}

function DropdownMenuContent({
  children,
  align = "end",
  side = "bottom",
  className,
}: DropdownMenuContentProps) {
  const { open, setOpen } = React.useContext(DropdownMenuContext);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleEscape);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open, setOpen]);

  if (!open) return null;

  const alignClass = {
    start: "left-0",
    center: "left-1/2 -translate-x-1/2",
    end: "right-0",
  }[align];

  return (
    <div
      ref={ref}
      role="menu"
      className={cn(
        "absolute z-50 min-w-[12rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
        side === "top" ? "bottom-full mb-2" : "mt-2",
        alignClass,
        className,
      )}
    >
      {children}
    </div>
  );
}

function DropdownMenuItem({
  children,
  className,
  asChild,
  onClick,
  ...props
}: DropdownMenuItemProps) {
  const { setOpen } = React.useContext(DropdownMenuContext);
  const itemClass = cn(
    "relative flex w-full cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none transition-colors hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground disabled:pointer-events-none disabled:opacity-50",
    className,
  );

  if (asChild && React.isValidElement(children)) {
    const child = children as React.ReactElement<{
      className?: string;
      onClick?: React.MouseEventHandler;
    }>;
    return React.cloneElement(child, {
      role: "menuitem",
      className: cn(itemClass, child.props.className),
      onClick: (event: React.MouseEvent) => {
        child.props.onClick?.(event);
        setOpen(false);
      },
    } as Record<string, unknown>);
  }

  return (
    <button
      type="button"
      role="menuitem"
      className={itemClass}
      onClick={(event) => {
        onClick?.(event);
        setOpen(false);
      }}
      {...props}
    >
      {children}
    </button>
  );
}

function DropdownMenuSeparator() {
  return <div role="separator" className="my-1 h-px bg-border" />;
}

function DropdownMenuLabel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "px-2 py-1.5 text-sm font-semibold text-foreground",
        className,
      )}
    >
      {children}
    </div>
  );
}

export {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger
};
