import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

// Extended profile type for Keycloak
interface KeycloakProfile {
  sub?: string;
  email?: string;
  name?: string;
  picture?: string;
  given_name?: string;
  family_name?: string;
  realm_access?: {
    roles?: string[];
  };
  groups?: string[];
}

interface KeycloakTokenResponse {
  access_token: string;
  expires_in: number;
  refresh_token?: string;
  id_token?: string;
}

async function refreshAccessToken(
  refreshToken: string | undefined,
): Promise<KeycloakTokenResponse | null> {
  if (!refreshToken) return null;
  try {
    const res = await fetch(
      `${process.env.KEYCLOAK_ISSUER}/protocol/openid-connect/token`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          client_id: process.env.KEYCLOAK_CLIENT_ID!,
          client_secret: process.env.KEYCLOAK_CLIENT_SECRET!,
          refresh_token: refreshToken,
        }),
      },
    );
    if (!res.ok) {
      console.warn("Keycloak token refresh failed:", res.status);
      return null;
    }
    return (await res.json()) as KeycloakTokenResponse;
  } catch (error) {
    console.warn("Keycloak token refresh failed:", error);
    return null;
  }
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    Keycloak({
      clientId: process.env.KEYCLOAK_CLIENT_ID!,
      clientSecret: process.env.KEYCLOAK_CLIENT_SECRET!,
      issuer: process.env.KEYCLOAK_ISSUER!,
    }),
  ],
  callbacks: {
    async jwt({ token, account, profile, trigger }) {
      // Persist the OAuth access_token to the token right after signin
      if (account) {
        token.accessToken = account.access_token;
        token.idToken = account.id_token;
        token.refreshToken = account.refresh_token;
        token.expiresAt = account.expires_at;
      }

      // Add user info from profile
      if (profile) {
        const kcProfile = profile as KeycloakProfile;
        token.sub = kcProfile.sub;
        token.email = kcProfile.email ?? undefined;
        token.name = kcProfile.name ?? undefined;
        token.picture = kcProfile.picture ?? undefined;
        token.given_name = kcProfile.given_name ?? undefined;
        token.family_name = kcProfile.family_name ?? undefined;
        // Use groups instead of roles
        token.groups = kcProfile.groups || [];

        // Mark that we need to fetch the picture from backend
        token.pictureFetched = false;
      }

      // Renew the access token a minute before it expires (api.ts caches it
      // for 30s, so callers never hold an expired one). A failed refresh —
      // revoked session, refresh token expired — returns null, which clears
      // the session so proxy.ts sends the user back to log in.
      const expiresAt = token.expiresAt as number | undefined;
      if (!account && expiresAt && Date.now() >= expiresAt * 1000 - 60_000) {
        const refreshed = await refreshAccessToken(token.refreshToken as string);
        if (!refreshed) return null;
        token.accessToken = refreshed.access_token;
        token.expiresAt = Math.floor(Date.now() / 1000) + refreshed.expires_in;
        token.refreshToken = refreshed.refresh_token ?? token.refreshToken;
        token.idToken = refreshed.id_token ?? token.idToken;
      }

      // Fetch picture from backend only once per session (or on update trigger)
      const shouldFetchPicture =
        token.accessToken && token.sub && !token.pictureFetched && !profile; // Don't fetch during initial login

      if (shouldFetchPicture || trigger === "update") {
        if (trigger === "update") {
          console.log("JWT callback - update trigger, fetching fresh data...");
        }

        try {
          const apiUrl =
            process.env.API_URL ||
            process.env.NEXT_PUBLIC_API_URL ||
            "http://localhost:8000/api";
          const response = await fetch(`${apiUrl}/account/me`, {
            headers: {
              Authorization: `Bearer ${token.accessToken}`,
            },
          });
          if (response.ok) {
            const userData = await response.json();
            // Mark as fetched even without a picture: otherwise every auth()
            // call (proxy.ts runs it on each navigation) re-hits /account/me,
            // which itself makes several blocking Keycloak Admin API calls.
            token.pictureFetched = true;
            if (userData.picture) {
              token.picture = userData.picture;
              if (trigger === "update") {
                console.log(
                  "JWT callback - updated token.picture:",
                  userData.picture,
                );
              }
            }
            // Sauvegarder le prénom/nom dans la session
            if (userData.given_name) token.given_name = userData.given_name;
            if (userData.family_name) token.family_name = userData.family_name;
            if (userData.name) token.name = userData.name;
          } else if (trigger === "update") {
            console.error(
              "JWT callback - failed to refresh user data:",
              response.status,
              response.statusText,
            );
          }
        } catch (error) {
          if (trigger === "update") {
            console.error("JWT callback - failed to refresh user data:", error);
          }
        }
      }

      return token;
    },
    async session({ session, token }) {
      // Send properties to the client
      session.accessToken = token.accessToken as string;
      session.idToken = token.idToken as string;
      session.user.id = token.sub as string;
      session.user.roles = token.groups as string[]; // Use groups as roles
      session.user.given_name = token.given_name as string;
      session.user.family_name = token.family_name as string;
      session.user.image = token.picture as string;

      return session;
    },
  },
  events: {
    async signOut() {
      // Federated logout is handled by the /api/auth/federated-logout route
      // This event is kept for any additional cleanup if needed
    },
  },
  pages: {
    signIn: "/auth/signin",
  },
  session: {
    strategy: "jwt",
  },
});
