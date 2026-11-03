"use client"

import * as React from "react"
import { Dialog as SheetPrimitive } from "@base-ui/react/dialog"

import { cn } from "@/lib/utils"

function Sheet({ ...props }: SheetPrimitive.Root.Props) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />
}

function SheetTrigger({ ...props }: SheetPrimitive.Trigger.Props) {
  return <SheetPrimitive.Trigger data-slot="sheet-trigger" {...props} />
}

function SheetClose({ ...props }: SheetPrimitive.Close.Props) {
  return <SheetPrimitive.Close data-slot="sheet-close" {...props} />
}

function SheetContent({
  className,
  children,
  side = "left",
  ...props
}: SheetPrimitive.Popup.Props & { side?: "left" | "right" }) {
  return (
    <SheetPrimitive.Portal data-slot="sheet-portal">
      <SheetPrimitive.Backdrop
        data-slot="sheet-overlay"
        className="fixed inset-0 z-50 bg-black/30 duration-150 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0"
      />
      <SheetPrimitive.Popup
        data-slot="sheet-content"
        className={cn(
          "fixed inset-y-0 z-50 flex w-72 max-w-[85vw] flex-col bg-card text-card-foreground shadow-lg outline-none duration-200 data-open:animate-in data-closed:animate-out",
          side === "left"
            ? "left-0 border-r data-open:slide-in-from-left data-closed:slide-out-to-left"
            : "right-0 border-l data-open:slide-in-from-right data-closed:slide-out-to-right",
          className
        )}
        {...props}
      >
        {children}
      </SheetPrimitive.Popup>
    </SheetPrimitive.Portal>
  )
}

function SheetTitle({ className, ...props }: SheetPrimitive.Title.Props) {
  return (
    <SheetPrimitive.Title
      data-slot="sheet-title"
      className={cn("font-heading text-base font-medium", className)}
      {...props}
    />
  )
}

function SheetDescription({
  className,
  ...props
}: SheetPrimitive.Description.Props) {
  return (
    <SheetPrimitive.Description
      data-slot="sheet-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetTitle, SheetDescription }
