import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";

export async function GET(request: NextRequest) {
  const origin = new URL(process.env.AUTH_URL || process.env.NEXTAUTH_URL || request.url).origin;
  const destination = new URL("/account", origin);
  destination.searchParams.set("github", "failed");
  const binding = request.cookies.get("cnp-github-oauth-binding")?.value;
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  let continuation: string | null = null;
  try {
    const session = await auth();
    if (session?.accessToken && !session.error && binding && state && !request.nextUrl.searchParams.has("error")) {
      const base = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
      const upstream = await fetch(`${base}/account/github/oauth/callback`, {
        method: "POST", cache: "no-store", redirect: "error",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.accessToken}` },
        body: JSON.stringify({ code, state, binding }),
        signal: AbortSignal.timeout(30000),
      });
      if (upstream.ok) {
        const result = await upstream.json();
        if (result.authorization_url) {
          const next = new URL(result.authorization_url);
          if (next.origin !== "https://github.com" || next.username || next.password || next.hash || next.pathname !== "/login/oauth/authorize") throw new Error("Unexpected authorization URL");
          continuation = next.toString();
        } else if (result.identity_linked === true) destination.searchParams.set("github", "linked");
      }
    }
  } catch {
    // Never put OAuth codes, tokens or upstream error bodies in logs or redirects.
  }
  const response = NextResponse.redirect(continuation || destination, 303);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.cookies.set("cnp-github-oauth-binding", continuation ? binding! : "", {
    httpOnly: true, sameSite: "lax", secure: origin.startsWith("https://"),
    path: "/api/auth/github", maxAge: continuation ? 600 : 0,
  });
  return response;
}
