"use client";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { appUrlOf } from "@/lib/utils";
import type { Deployment } from "@/types";
import { ChevronDown, ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";

const hostOf = (url: string) => url.replace(/^https?:\/\//, "").replace(/\/.*$/, "");

const LINK_CLASS =
  "inline-flex items-center gap-1 whitespace-nowrap text-primary hover:underline";

// inLink: the parent is a Link, so open from a button without navigating.
export function EnvLinks({
  prod,
  staging,
  inLink = false,
}: {
  prod?: string | null;
  staging?: string | null;
  inLink?: boolean;
}) {
  const t = useTranslations("apps.env");
  const items = [
    { label: t("prod"), url: prod },
    { label: t("staging"), url: staging },
  ].flatMap(({ label, url }) => (url ? [{ label, url }] : []));

  return (
    <>
      {items.map(({ label, url }) => {
        const host = hostOf(url);
        const common = {
          title: host,
          "aria-label": `${label}, ${host}, ${t("newTab")}`,
          className: LINK_CLASS,
        };
        return inLink ? (
          <button
            key={label}
            type="button"
            {...common}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              window.open(url, "_blank", "noopener,noreferrer");
            }}
          >
            {label}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </button>
        ) : (
          <a
            key={label}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            {...common}
          >
            {label}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        );
      })}
    </>
  );
}

export function OpenAppMenu({ apps }: { apps: Deployment[] }) {
  const t = useTranslations("apps.env");
  const pub = apps.flatMap((app) => {
    const prod = app.status === "running" ? appUrlOf(app.terraform_outputs) : null;
    return prod ? [{ app, prod }] : [];
  });
  if (pub.length === 0) return null;

  if (pub.length === 1) {
    const { app, prod } = pub[0];
    return (
      <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
        {t("openAppLabel")}
        <EnvLinks prod={prod} staging={app.staging_url} />
      </span>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          {t("openApp")}
          <ChevronDown aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[14rem]">
        {pub.flatMap(({ app, prod }) =>
          [
            { url: prod, label: app.name },
            { url: app.staging_url, label: t("stagingOf", { app: app.name }) },
          ].flatMap(({ url, label }) =>
            url
              ? [
                  <DropdownMenuItem key={url} asChild className="gap-2">
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`${label}, ${t("newTab")}`}
                    >
                      {label}
                      <ExternalLink className="ml-auto h-3 w-3" aria-hidden />
                    </a>
                  </DropdownMenuItem>,
                ]
              : [],
          ),
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
