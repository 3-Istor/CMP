import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";

import {
  apiBase,
  BINDING_COOKIE,
  BINDING_MAX_AGE,
  BINDING_PATH,
  isGitHubAuthorizeUrl,
  publicOrigin,
} from "../shared";

// GitHub redirects here after an authorization or an installation. The code
// and state go to the API only, never into a redirect or a log.
export async function GET(request: NextRequest) {
  const origin = publicOrigin(request.url);
  const destination = new URL("/account", origin);
  destination.searchParams.set("github", "failed");
  const binding = request.cookies.get(BINDING_COOKIE)?.value;
  const params = request.nextUrl.searchParams;
  const code = params.get("code");
  const state = params.get("state");
  let continuation: string | null = null;
  try {
    const session = await auth();
    if (
      session?.accessToken &&
      !session.error &&
      binding &&
      state &&
      !params.has("error")
    ) {
      const upstream = await fetch(
        `${apiBase()}/account/github/oauth/callback`,
        {
          method: "POST",
          cache: "no-store",
          redirect: "error",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${session.accessToken}`,
          },
          body: JSON.stringify({ code, state, binding }),
          signal: AbortSignal.timeout(30000),
        },
      );
      if (upstream.ok) {
        const result = await upstream.json();
        if (result.authorization_url) {
          // After an installation: continue with a PKCE authorization.
          const next = new URL(result.authorization_url);
          if (!isGitHubAuthorizeUrl(next)) {
            throw new Error("Unexpected GitHub destination");
          }
          continuation = next.toString();
        } else if (result.identity_linked === true) {
          destination.searchParams.set("github", "linked");
        }
      }
    }
  } catch {
    // Falls through to /account?github=failed.
  }
  const response = NextResponse.redirect(continuation || destination, 303);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.cookies.set(BINDING_COOKIE, continuation && binding ? binding : "", {
    httpOnly: true,
    sameSite: "lax",
    secure: origin.startsWith("https://"),
    path: BINDING_PATH,
    maxAge: continuation ? BINDING_MAX_AGE : 0,
  });
  return response;
}
