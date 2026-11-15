"use client";

import { getCurrentUser } from "@/lib/api";
import { useEffect, useState } from "react";

let answer: Promise<string[]> | null = null;

function fetchPlatformRoles(): Promise<string[]> {
  answer ??= getCurrentUser()
    .then((profile) => profile.platform_roles ?? [])
    .catch(() => {
      answer = null;
      return [];
    });
  return answer;
}

/** The caller's platform groups (infra, security-auditor, finops). */
export function usePlatformRoles(): string[] {
  const [roles, setRoles] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchPlatformRoles().then((value) => {
      if (!cancelled) setRoles(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return roles;
}
