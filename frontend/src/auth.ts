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
            if (userData.picture) {
              token.picture = userData.picture;
              token.pictureFetched = true;
              if (trigger === "update") {
                console.log(
                  "JWT callback - updated token.picture:",
                  userData.picture,
                );
              }
            }
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
