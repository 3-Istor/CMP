import { Toaster } from "@/components/ui/sonner";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ARCL CMP — Hybrid Cloud Management",
  description: "Self-service hybrid cloud management platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Get API URL from environment variable at runtime
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

  return (
    <html lang="en" className="dark">
      <head>
        {/* Inject runtime config inline */}
        <script
          dangerouslySetInnerHTML={{
            __html: `window.__RUNTIME_CONFIG__ = { apiUrl: '${apiUrl}' };`,
          }}
        />
      </head>
      <body
        className={`${inter.variable} font-sans antialiased bg-background text-foreground`}
      >
        {children}
        <Toaster richColors position="bottom-right" />
      </body>
    </html>
  );
}
