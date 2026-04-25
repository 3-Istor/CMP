import { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    accessToken?: string;
    idToken?: string;
    user: {
      id: string;
      roles: string[];
      given_name?: string;
      family_name?: string;
    } & DefaultSession["user"];
  }

  interface JWT {
    accessToken?: string;
    idToken?: string;
    refreshToken?: string;
    expiresAt?: number;
    groups?: string[];
    given_name?: string;
    family_name?: string;
    pictureFetched?: boolean;
  }
}
