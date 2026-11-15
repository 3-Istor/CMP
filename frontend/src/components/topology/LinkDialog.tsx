"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { aliasProblem, type GraphNode } from "@/lib/topology";

export const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground";

export interface LinkRequest {
  from: string;
  to: string;
  port: number;
  alias: string;
}

function messageOf(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  try {
    const detail = JSON.parse(raw)?.detail;
    if (typeof detail === "string") return detail;
  } catch {
    // not JSON
  }
  return raw;
}

export function LinkDialog({
  open,
  onOpenChange,
  nodes,
  initialFrom,
  initialTo,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Apps that can be linked: imported services and backing apps. */
  nodes: GraphNode[];
  initialFrom?: string;
  initialTo?: string;
  onSubmit: (link: LinkRequest) => Promise<void>;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open && (
        <LinkForm
          key={`${initialFrom}-${initialTo}`}
          nodes={nodes}
          initialFrom={initialFrom}
          initialTo={initialTo}
          onSubmit={onSubmit}
          onClose={() => onOpenChange(false)}
        />
      )}
    </Dialog>
  );
}

function LinkForm({
  nodes,
  initialFrom,
  initialTo,
  onSubmit,
  onClose,
}: {
  nodes: GraphNode[];
  initialFrom?: string;
  initialTo?: string;
  onSubmit: (link: LinkRequest) => Promise<void>;
  onClose: () => void;
}) {
  const t = useTranslations("topology.link");
  const tAlias = useTranslations("topology.alias");
  const ids = { from: useId(), to: useId(), port: useId(), alias: useId(), error: useId() };
  const [from, setFrom] = useState(initialFrom ?? nodes[0]?.id ?? "");
  const [to, setTo] = useState(initialTo ?? nodes.find((n) => n.id !== from)?.id ?? "");
  const target = nodes.find((n) => n.id === to);
  const source = nodes.find((n) => n.id === from);
  const [port, setPort] = useState<number | null>(target?.ports[0]?.port ?? null);
  const [alias, setAlias] = useState(target?.name ?? "");
  const [aliasEdited, setAliasEdited] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chooseTarget = (id: string) => {
    setTo(id);
    const next = nodes.find((n) => n.id === id);
    setPort(next?.ports[0]?.port ?? null);
    if (!aliasEdited) setAlias(next?.name ?? "");
  };

  const problem = source ? aliasProblem(alias, source.name) : null;
  const valid = Boolean(source && target && from !== to && port && !problem);

  const submit = async () => {
    if (!valid || !source || !target || !port || pending) return;
    setPending(true);
    setError(null);
    try {
      await onSubmit({ from: source.name, to: target.name, port, alias });
      onClose();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>{t("title")}</DialogTitle>
        <DialogDescription>{t("description")}</DialogDescription>
      </DialogHeader>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor={ids.from}>{t("from")}</Label>
            <select id={ids.from} value={from} onChange={(e) => {
                const next = e.target.value;
                setFrom(next);
                if (next === to) chooseTarget(nodes.find((n) => n.id !== next)?.id ?? "");
              }} className={SELECT_CLASS}>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.to}>{t("to")}</Label>
            <select id={ids.to} value={to} onChange={(e) => chooseTarget(e.target.value)} className={SELECT_CLASS}>
              {nodes
                .filter((n) => n.id !== from)
                .map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
            </select>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.port}>{t("port")}</Label>
          {target && target.ports.length > 0 ? (
            <select
              id={ids.port}
              value={port ?? ""}
              onChange={(e) => setPort(Number(e.target.value))}
              className={SELECT_CLASS}
            >
              {target.ports.map((p) => (
                <option key={p.port} value={p.port}>
                  {p.port} ({p.name})
                </option>
              ))}
            </select>
          ) : (
            <p id={ids.port} className="text-sm text-muted-foreground">
              {t("noPort")}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.alias}>{t("alias")}</Label>
          <Input
            id={ids.alias}
            value={alias}
            onChange={(e) => {
              setAlias(e.target.value);
              setAliasEdited(true);
            }}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={problem ? true : undefined}
            aria-describedby={ids.error}
          />
          <p id={ids.error} className={problem ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
            {problem ? tAlias(problem) : t("aliasHelp", { alias: alias || "...", port: port ?? "" })}
          </p>
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            {t("cancel")}
          </Button>
          <Button type="submit" disabled={!valid || pending}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            {t("submit")}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
