import { StepUpNotice } from "@/components/auth/StepUpNotice";
import { AppShell } from "@/components/layout/AppShell";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { Toaster } from "@/components/ui/sonner";
import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";
import { Inter } from "next/font/google";
import "./globals.css";

export const dynamic = "force-dynamic";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("shell.meta");
  return { title: t("title"), description: t("description") };
}

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Get API URL from environment variable at runtime
  const locale = await getLocale();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

  return (
    // The theme class is set by the script below, before first paint.
    <html lang={locale} suppressHydrationWarning>
      <head>
        {/* Inject runtime config inline */}
        <script
          dangerouslySetInnerHTML={{
            __html: `window.__RUNTIME_CONFIG__ = { apiUrl: '${apiUrl}' };`,
          }}
        />
        {/* Follows the browser unless the user picked light or dark on
            the sidebar (ThemeSwitch, which calls __applyTheme after a change). */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var m=matchMedia('(prefers-color-scheme: dark)');window.__applyTheme=function(){var t;try{t=localStorage.getItem('cnp-theme')}catch(e){}document.documentElement.classList.toggle('dark',t==='dark'||(t!=='light'&&m.matches))};m.addEventListener('change',window.__applyTheme);window.__applyTheme()})()`,
          }}
        />
      </head>
      <body
        className={`${inter.variable} font-sans antialiased bg-background text-foreground`}
      >
        <NextIntlClientProvider>
          <SessionProvider>
            <AppShell>{children}</AppShell>
            <Toaster richColors position="bottom-right" />
            <StepUpNotice />
          </SessionProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
