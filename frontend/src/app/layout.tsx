import { AppShell } from "@/components/layout/AppShell";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { Toaster } from "@/components/ui/sonner";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

export const dynamic = "force-dynamic";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "CNP - Cloud Native Platform",
  description: "Self-service cloud native platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Get API URL from environment variable at runtime
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

  return (
    // The theme class is set by the script below, before first paint.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Inject runtime config inline */}
        <script
          dangerouslySetInnerHTML={{
            __html: `window.__RUNTIME_CONFIG__ = { apiUrl: '${apiUrl}' };`,
          }}
        />
        {/* Dark unless the user picked light on /account (ThemeCard) */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{if(localStorage.getItem('cnp-theme')!=='light')document.documentElement.classList.add('dark')}catch(e){document.documentElement.classList.add('dark')}`,
          }}
        />
      </head>
      <body
        className={`${inter.variable} font-sans antialiased bg-background text-foreground`}
      >
        <SessionProvider>
          <AppShell>{children}</AppShell>
          <Toaster richColors position="bottom-right" />
        </SessionProvider>
      </body>
    </html>
  );
}
