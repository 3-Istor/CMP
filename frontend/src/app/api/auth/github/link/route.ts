import { randomBytes } from "node:crypto";

import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";

import {
  apiBase,
  BINDING_COOKIE,
  BINDING_MAX_AGE,
  BINDING_PATH,
  isGitHubAuthorizeUrl,
  isGitHubInstallUrl,
  publicOrigin,
} from "../shared";

export async function POST(request: NextRequest) {
  const origin = publicOrigin(request.url);
  if (request.headers.get("origin") !== origin) {
    return NextResponse.json({ detail: "Origin refused" }, { status: 403 });
  }
  const session = await auth().catch(() => null);
  if (!session?.accessToken || session.error) {
    return NextResponse.json({ detail: "Sign in again" }, { status: 401 });
  }
  const input = await request.json().catch(() => null);
  const mode = input?.mode ?? "authorize";
  if (mode !== "authorize" && mode !== "install") {
    return NextResponse.json(
      { detail: "Invalid GitHub flow" },
      { status: 422 },
    );
  }
  try {
    const binding = randomBytes(48).toString("base64url");
    const upstream = await fetch(`${apiBase()}/account/github/oauth/start`, {
      method: "POST",
      cache: "no-store",
      redirect: "error",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.accessToken}`,
      },
      body: JSON.stringify({ binding, mode }),
      signal: AbortSignal.timeout(30000),
    });
    // Any refusal stays generic: its body never reaches the browser.
    if (!upstream.ok) throw new Error("GitHub linking refused");
    const result = await upstream.json();
    const destination = new URL(result.authorization_url);
    if (
      !isGitHubAuthorizeUrl(destination) &&
      !isGitHubInstallUrl(destination)
    ) {
      throw new Error("Unexpected GitHub destination");
    }
    const response = NextResponse.json({
      authorization_url: destination.toString(),
    });
    response.headers.set("Cache-Control", "no-store");
    response.cookies.set(BINDING_COOKIE, binding, {
      httpOnly: true,
      sameSite: "lax",
      secure: origin.startsWith("https://"),
      path: BINDING_PATH,
      maxAge: BINDING_MAX_AGE,
    });
    return response;
  } catch {
    return NextResponse.json(
      { detail: "GitHub linking is unavailable" },
      { status: 503 },
    );
  }
}
