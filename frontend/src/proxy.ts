import { auth } from "@/auth";
import { NextResponse } from "next/server";

export default auth((req) => {
  const isLoggedIn = !!req.auth;
  const { pathname } = req.nextUrl;
  const isAuthPage = pathname.startsWith("/auth");
  const isWelcomePage = pathname === "/welcome";

  // Auth pages (Keycloak redirect dance) are always reachable.
  if (isAuthPage) {
    return NextResponse.next();
  }

  // The public landing page is for visitors who aren't signed in yet — send
  // authenticated users straight to the app instead.
  if (isWelcomePage) {
    if (isLoggedIn) {
      return NextResponse.redirect(new URL("/", req.url));
    }
    return NextResponse.next();
  }

  // Everything else is protected: unauthenticated users land on the marketing
  // homepage, which carries the original destination so we can return there
  // once they log in.
  if (!isLoggedIn) {
    const welcomeUrl = new URL("/welcome", req.url);
    welcomeUrl.searchParams.set("callbackUrl", pathname);
    return NextResponse.redirect(welcomeUrl);
  }

  return NextResponse.next();
});

export const config = {
  matcher: [
    // Exclude API, Next internals, and static public assets (images, fonts…).
    // Without the file-extension guard, next/image's internal fetch of
    // /logo.svg gets redirected to /welcome and the logo fails to load.
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.[\\w]+$).*)",
  ],
};
