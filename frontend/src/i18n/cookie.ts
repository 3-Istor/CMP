import { LOCALE_COOKIE, type Locale } from "./config";

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

export function setLocaleCookie(locale: Locale) {
  document.cookie = `${LOCALE_COOKIE}=${locale}; path=/; max-age=${ONE_YEAR_SECONDS}; samesite=lax`;
}
