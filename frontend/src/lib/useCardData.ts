"use client";

import { ApiError } from "@/lib/api";
import { useCallback, useEffect, useRef, useState } from "react";

export interface CardData<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  forbidden: boolean;
  stale: boolean;
  updatedAt: Date | null;
  retry: () => void;
}

/**
 * Fetches one card's data on its own, so a failing card never empties the
 * page. A failed refresh keeps the last data and flags it as stale.
 */
export function useCardData<T>(
  key: string,
  fetcher: () => Promise<T>,
  { intervalMs = 0, enabled = true }: { intervalMs?: number; enabled?: boolean } = {},
): CardData<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const fetcherRef = useRef(fetcher);
  const latest = useRef(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const load = useCallback(async () => {
    const ticket = ++latest.current;
    try {
      const result = await fetcherRef.current();
      if (ticket !== latest.current) return;
      setData(result);
      setError(null);
      setForbidden(false);
      setUpdatedAt(new Date());
    } catch (err) {
      if (ticket !== latest.current) return;
      if (err instanceof ApiError && err.status === 403) {
        setForbidden(true);
        setData(null);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (ticket === latest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      latest.current++;
      return;
    }
    setLoading(true);
    setData(null);
    setError(null);
    setForbidden(false);
    void load();
  }, [key, enabled, load]);

  useEffect(() => {
    if (!enabled || intervalMs <= 0) return;
    const timer = setInterval(() => void load(), intervalMs);
    return () => clearInterval(timer);
  }, [enabled, intervalMs, load]);

  const retry = useCallback(() => {
    setLoading(true);
    void load();
  }, [load]);

  return {
    data,
    loading,
    error,
    forbidden,
    stale: error !== null && data !== null,
    updatedAt,
    retry,
  };
}
