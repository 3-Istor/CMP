// Bridge between the browser and the CMP API for GitHub linking. The random
// binding lives only in an httpOnly cookie scoped to these routes: a GitHub
// callback opened in another browser, or by another session, cannot
// complete the link (the API ties the OAuth state to it).

export const BINDING_COOKIE = "cnp-github-oauth-binding";
export const BINDING_PATH = "/api/auth/github";
export const BINDING_MAX_AGE = 600;

export function publicOrigin(requestUrl: string): string {
  return new URL(process.env.AUTH_URL || process.env.NEXTAUTH_URL || requestUrl)
    .origin;
}

export function apiBase(): string {
  return (
    process.env.API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000/api"
  );
}

export function isGitHubAuthorizeUrl(url: URL): boolean {
  return (
    url.origin === "https://github.com" &&
    !url.username &&
    !url.password &&
    !url.hash &&
    url.pathname === "/login/oauth/authorize"
  );
}

export function isGitHubInstallUrl(url: URL): boolean {
  return (
    url.origin === "https://github.com" &&
    !url.username &&
    !url.password &&
    !url.hash &&
    /^\/apps\/[A-Za-z0-9-]+\/installations\/new$/.test(url.pathname)
  );
}
