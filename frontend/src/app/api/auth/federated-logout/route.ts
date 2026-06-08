import { auth, signOut } from "@/auth";
import { NextResponse } from "next/server";

export async function POST() {
  try {
    const session = await auth();

    if (!session) {
      return NextResponse.json({ error: "No active session" }, { status: 401 });
    }

    // Get the idToken before signing out
    const idToken = session.idToken;

    // Sign out from NextAuth (clears local session)
    await signOut({ redirect: false });

    // Build Keycloak logout URL for browser redirect
    const keycloakIssuer = process.env.KEYCLOAK_ISSUER!;
    const logoutUrl = `${keycloakIssuer}/protocol/openid-connect/logout`;

    // Ensure the redirect URI has a trailing slash for exact matching
    let postLogoutRedirectUri =
      process.env.NEXTAUTH_URL || "http://localhost:3000";
    if (!postLogoutRedirectUri.endsWith("/")) {
      postLogoutRedirectUri += "/";
    }

    const params = new URLSearchParams({
      post_logout_redirect_uri: postLogoutRedirectUri,
    });

    // Add id_token_hint if available (required for seamless logout)
    if (idToken) {
      params.set("id_token_hint", idToken);
    }

    // Return the Keycloak logout URL for client-side redirect
    return NextResponse.json({
      logoutUrl: `${logoutUrl}?${params.toString()}`,
    });
  } catch (error) {
    console.error("Federated logout error:", error);
    return NextResponse.json({ error: "Logout failed" }, { status: 500 });
  }
}
