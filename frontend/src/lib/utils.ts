import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function appUrlOf(terraformOutputs: string | null): string | null {
  try {
    const url = JSON.parse(terraformOutputs ?? "{}").app_url
    return typeof url === "string" && url.startsWith("https://") ? url : null
  } catch {
    return null
  }
}
