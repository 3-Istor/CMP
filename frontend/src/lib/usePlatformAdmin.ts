"use client";

import { getCurrentUser } from "@/lib/api";
import { useEffect, useState } from "react";

let answer: Promise<boolean> | null = null;

function fetchPlatformAdmin(): Promise<boolean> {
  answer ??= getCurrentUser()
    .then((profile) => profile.cnp_admin === true)
    .catch(() => {
      answer = null;
      return false;
    });
  return answer;
}

export function usePlatformAdmin(): boolean {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchPlatformAdmin().then((value) => {
      if (!cancelled) setIsAdmin(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return isAdmin;
}
