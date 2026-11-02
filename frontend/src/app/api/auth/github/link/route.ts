import { randomBytes } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";

export async function POST(request: NextRequest) {
  const origin = new URL(process.env.AUTH_URL || process.env.NEXTAUTH_URL || request.url).origin;
  if (request.headers.get("origin") !== origin) {
    return NextResponse.json({ detail: "Origine de connexion refusée" }, { status: 403 });
  }
  try {
    const session = await auth();
    if (!session?.accessToken || session.error) {
      return NextResponse.json({ detail: "Reconnectez-vous à CNP" }, { status: 401 });
    }
    const binding = randomBytes(48).toString("base64url");
    const input = await request.json().catch(() => ({}));
    const mode = input?.mode ?? "authorize";
    if (mode !== "authorize" && mode !== "install") {
      return NextResponse.json({ detail: "Parcours GitHub invalide" }, { status: 422 });
    }
    const base = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
    const upstream = await fetch(`${base}/account/github/oauth/start`, {
      method: "POST", cache: "no-store", redirect: "error",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.accessToken}` },
      body: JSON.stringify({ binding, mode }),
      signal: AbortSignal.timeout(30000),
    });
    if (!upstream.ok) throw new Error("OAuth unavailable");
    const result = await upstream.json();
    const destination = new URL(result.authorization_url);
    if (destination.origin !== "https://github.com" || destination.username || destination.password || destination.hash || !(
      destination.pathname === "/login/oauth/authorize" || /^\/apps\/[A-Za-z0-9-]+\/installations\/new$/.test(destination.pathname)
    )) {
      throw new Error("Unexpected OAuth destination");
    }
    const response = NextResponse.json({ authorization_url: destination.toString() });
    response.headers.set("Cache-Control", "no-store");
    response.cookies.set("cnp-github-oauth-binding", binding, {
      httpOnly: true, sameSite: "lax", secure: origin.startsWith("https://"),
      path: "/api/auth/github", maxAge: 600,
    });
    return response;
  } catch {
    return NextResponse.json({ detail: "La connexion OAuth GitHub est indisponible ou non configurée" }, { status: 503 });
  }
}
