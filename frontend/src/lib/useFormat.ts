"use client";

import { useFormatter } from "next-intl";

type NumberOptions = Parameters<ReturnType<typeof useFormatter>["number"]>[1];

export function useFormat() {
  const formatter = useFormatter();
  return {
    date: (value: Date | string | number) =>
      formatter.dateTime(new Date(value), {
        day: "numeric",
        month: "short",
        year: "numeric",
      }),
    dateTime: (value: Date | string | number) =>
      formatter.dateTime(new Date(value), {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
    relative: (value: Date | string | number) =>
      formatter.relativeTime(new Date(value)),
    number: (value: number, options?: NumberOptions) =>
      formatter.number(value, options),
  };
}
