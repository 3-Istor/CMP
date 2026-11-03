"use client";

import { signIn } from "next-auth/react";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useEffect } from "react";

export default function SignInPage() {
  const t = useTranslations("signin");
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") || "/";

  useEffect(() => {
    // Automatically redirect to Keycloak
    signIn("keycloak", { callbackUrl });
  }, [callbackUrl]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="text-center">
        <h1 className="text-2xl font-bold mb-4">{t("redirecting")}</h1>
        <p className="text-muted-foreground">{t("wait")}</p>
      </div>
    </div>
  );
}
