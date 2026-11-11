"use client";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  getNotifications,
  getUnreadNotificationCount,
  markAllNotificationsRead,
  markNotificationRead,
  type AppNotification,
  type NotificationKind,
} from "@/lib/api";
import { useFormat } from "@/lib/useFormat";
import { cn } from "@/lib/utils";
import {
  Bell,
  CheckCircle2,
  KeyRound,
  ShieldQuestion,
  Trash2,
  TriangleAlert,
  UserPlus,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

const POLL_INTERVAL_MS = 30_000;
const PAGE_SIZE = 20;

const KIND_ICONS: Record<NotificationKind, LucideIcon> = {
  deployment_succeeded: CheckCircle2,
  deployment_failed: XCircle,
  app_deleted: Trash2,
  alert: TriangleAlert,
  member_added: UserPlus,
  approval_request: ShieldQuestion,
  access: KeyRound,
};

const KIND_TONES: Record<NotificationKind, string> = {
  deployment_succeeded: "text-green-600 dark:text-green-400",
  deployment_failed: "text-red-600 dark:text-red-400",
  app_deleted: "text-muted-foreground",
  alert: "text-amber-600 dark:text-amber-400",
  member_added: "text-primary",
  approval_request: "text-primary",
  access: "text-amber-600 dark:text-amber-400",
};

function useUnreadCount(): [number, () => Promise<void>] {
  const [count, setCount] = useState(0);

  const refresh = useCallback(async () => {
    try {
      setCount(await getUnreadNotificationCount());
    } catch {
      // The next tick retries; a bell that fails quietly beats an error toast every 30 s.
    }
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (timer) return;
      void refresh();
      timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    };
    const stop = () => {
      if (timer) clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };

    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [refresh]);

  return [count, refresh];
}

export function NotificationCenter({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const t = useTranslations("notifications");
  const [count, refreshCount] = useUnreadCount();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getNotifications({ limit: PAGE_SIZE })
      .then((page) => {
        if (cancelled) return;
        setItems(page);
        setHasMore(page.length === PAGE_SIZE);
        setFailed(false);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, count]);

  const loadMore = async () => {
    const last = items[items.length - 1];
    if (!last) return;
    setLoading(true);
    try {
      const page = await getNotifications({ limit: PAGE_SIZE, before: last.id });
      setItems((current) => [...current, ...page]);
      setHasMore(page.length === PAGE_SIZE);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  };

  const markRead = async (item: AppNotification) => {
    if (item.read_at) return;
    setItems((current) =>
      current.map((n) =>
        n.id === item.id ? { ...n, read_at: new Date().toISOString() } : n,
      ),
    );
    try {
      await markNotificationRead(item.id);
    } finally {
      void refreshCount();
    }
  };

  const markAllRead = async () => {
    const now = new Date().toISOString();
    setItems((current) => current.map((n) => ({ ...n, read_at: n.read_at ?? now })));
    try {
      await markAllNotificationsRead();
    } finally {
      void refreshCount();
    }
  };

  const label = t("bell", { count });

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={t("title")}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground/80 transition-colors hover:bg-accent hover:text-foreground",
          collapsed && "justify-center",
        )}
      >
        <span className="relative flex">
          <Bell className="h-4 w-4 shrink-0" aria-hidden />
          {collapsed && count > 0 && (
            <span
              className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-red-600"
              aria-hidden
            />
          )}
        </span>
        {!collapsed && <span className="flex-1 truncate text-left">{t("title")}</span>}
        {!collapsed && count > 0 && (
          <span
            aria-hidden
            className="min-w-5 rounded-full bg-red-600 px-1.5 text-center text-[11px] font-medium tabular-nums text-white"
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-96">
          <div className="flex items-start justify-between gap-2 border-b p-4">
            <div className="min-w-0">
              <SheetTitle>{t("title")}</SheetTitle>
              <SheetDescription className="sr-only">
                {t("description")}
              </SheetDescription>
            </div>
            <button
              type="button"
              onClick={() => void markAllRead()}
              disabled={count === 0}
              className="shrink-0 rounded-md px-2 py-1 text-sm text-primary transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
            >
              {t("markAllRead")}
            </button>
          </div>

          <div className="flex-1 overflow-y-auto" aria-busy={loading}>
            {failed && (
              <p role="alert" className="p-4 text-sm text-red-600 dark:text-red-400">
                {t("error")}
              </p>
            )}
            {!failed && !loading && items.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">{t("empty")}</p>
            )}
            <ul className="divide-y">
              {items.map((item) => (
                <NotificationRow
                  key={item.id}
                  item={item}
                  onOpen={() => {
                    void markRead(item);
                    setOpen(false);
                    onNavigate?.();
                  }}
                />
              ))}
            </ul>
            {hasMore && (
              <div className="p-2">
                <button
                  type="button"
                  onClick={() => void loadMore()}
                  disabled={loading}
                  className="w-full rounded-md px-2 py-1.5 text-sm text-primary transition-colors hover:bg-accent disabled:opacity-50"
                >
                  {t("loadMore")}
                </button>
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

function NotificationRow({
  item,
  onOpen,
}: {
  item: AppNotification;
  onOpen: () => void;
}) {
  const t = useTranslations("notifications");
  const format = useFormat();
  const Icon = KIND_ICONS[item.kind] ?? Bell;
  const unread = item.read_at === null;
  const titleKey = `titles.${item.title_key}`;
  const title = t.has(titleKey)
    ? t(titleKey, item.params)
    : t("titles.unknown");

  const content = (
    <>
      <Icon
        className={cn("mt-0.5 h-4 w-4 shrink-0", KIND_TONES[item.kind])}
        aria-hidden
      />
      <span className="min-w-0 flex-1">
        <span className={cn("block text-sm", unread && "font-medium")}>
          {title}
        </span>
        <time
          dateTime={item.created_at}
          className="block text-xs text-muted-foreground"
        >
          {format.relative(item.created_at)}
        </time>
      </span>
      {unread && (
        <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary">
          <span className="sr-only">{t("unread")}</span>
        </span>
      )}
    </>
  );

  const className = cn(
    "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-accent",
    unread && "bg-primary/5",
  );

  return (
    <li>
      {item.link ? (
        <Link href={item.link} onClick={onOpen} className={className}>
          {content}
        </Link>
      ) : (
        <button type="button" onClick={onOpen} className={className}>
          {content}
        </button>
      )}
    </li>
  );
}
