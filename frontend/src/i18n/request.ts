import { auth } from "@/auth";
import { getRequestConfig } from "next-intl/server";
import { cookies, headers } from "next/headers";
import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  isLocale,
  localeFromAcceptLanguage,
  type Locale,
} from "./config";

const PREFERENCE_TIMEOUT_MS = 1500;

async function profileLocale(): Promise<Locale | null> {
  try {
    const session = await auth();
    if (!session?.accessToken) return null;
    const apiUrl =
      process.env.API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://localhost:8000/api";
    const response = await fetch(`${apiUrl}/account/preferences`, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
      cache: "no-store",
      signal: AbortSignal.timeout(PREFERENCE_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    const { locale } = await response.json();
    return isLocale(locale) ? locale : null;
  } catch {
    return null;
  }
}

// The switcher writes the cookie and the profile together, so the profile is only read on a device without the cookie.
async function resolveLocale(): Promise<Locale> {
  const fromCookie = (await cookies()).get(LOCALE_COOKIE)?.value;
  if (isLocale(fromCookie)) return fromCookie;

  const fromProfile = await profileLocale();
  if (fromProfile) return fromProfile;

  const fromHeader = localeFromAcceptLanguage(
    (await headers()).get("accept-language"),
  );
  return fromHeader ?? DEFAULT_LOCALE;
}

export default getRequestConfig(async () => {
  const locale = await resolveLocale();
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
