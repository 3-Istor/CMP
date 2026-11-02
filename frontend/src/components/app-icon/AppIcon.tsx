"use client";

import { TemplateIcon } from "@/components/catalog/TemplateIcon";
import { fetchAppIconBlob } from "@/lib/api";
import type { AppIconValue } from "@/types";
import { useEffect, useState } from "react";
import {
  BUILTIN_ICONS,
  COLOR_TEXT,
  type BuiltinIconName,
  type IconColor,
} from "./catalog";

const blobUrls = new Map<string, Promise<string>>();

function uploadedIconUrl(appId: number, hash: string): Promise<string> {
  const key = `${appId}:${hash}`;
  let url = blobUrls.get(key);
  if (!url) {
    url = fetchAppIconBlob(appId).then((blob) => URL.createObjectURL(blob));
    url.catch(() => blobUrls.delete(key));
    blobUrls.set(key, url);
  }
  return url;
}

interface AppIconProps {
  appId: number;
  templateIcon?: string | null;
  icon?: AppIconValue | null;
  className?: string;
}

function UploadedIcon({
  appId,
  hash,
  templateIcon,
  className,
}: {
  appId: number;
  hash: string;
  templateIcon?: string | null;
  className: string;
}) {
  const [state, setState] = useState<{
    key: string;
    url: string | null;
    failed: boolean;
  }>({ key: "", url: null, failed: false });
  const key = `${appId}:${hash}`;

  useEffect(() => {
    let cancelled = false;
    uploadedIconUrl(appId, hash).then(
      (url) => !cancelled && setState({ key, url, failed: false }),
      () => !cancelled && setState({ key, url: null, failed: true }),
    );
    return () => {
      cancelled = true;
    };
  }, [appId, hash, key]);

  const current = state.key === key ? state : null;
  if (current?.failed) {
    return <TemplateIcon icon={templateIcon} className={className} />;
  }
  if (!current?.url) {
    return <span className={`${className} inline-block`} aria-hidden="true" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={current.url}
      alt=""
      aria-hidden="true"
      className={`${className} rounded-md object-cover`}
      onError={() => setState({ key, url: null, failed: true })}
    />
  );
}

export function AppIcon({
  appId,
  templateIcon,
  icon,
  className = "h-6 w-6",
}: AppIconProps) {
  if (icon?.kind === "builtin" && icon.name in BUILTIN_ICONS) {
    const Icon = BUILTIN_ICONS[icon.name as BuiltinIconName];
    const color = COLOR_TEXT[icon.color as IconColor] ?? COLOR_TEXT.indigo;
    return <Icon className={`${className} ${color}`} aria-hidden="true" />;
  }
  if (icon?.kind === "upload") {
    return (
      <UploadedIcon
        appId={appId}
        hash={icon.hash}
        templateIcon={templateIcon}
        className={className}
      />
    );
  }
  return <TemplateIcon icon={templateIcon} className={className} />;
}
