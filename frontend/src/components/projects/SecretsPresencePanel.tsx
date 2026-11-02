"use client";

import { ADMIN_LINKS } from "@/components/layout/shellData";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getSecretPresence } from "@/lib/api";
import type { SecretPresence } from "@/types";
import {
    CheckCircle2,
    ExternalLink,
    KeyRound,
    RefreshCw,
    XCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

interface Props {
    deploymentId: number;
}

const VAULT_URL = ADMIN_LINKS.find((link) => link.key === "vault")?.href ?? "";

const renderCode = (chunks: React.ReactNode) => (
    <span className="font-mono">{chunks}</span>
);

function vaultEditUrl(vaultPath: string): string {
    const [mount, ...path] = vaultPath.split("/");
    return `${VAULT_URL}/ui/vault/secrets/${mount}/kv/${path.join("/")}/edit`;
}

export function SecretsPresencePanel({ deploymentId }: Props) {
    const t = useTranslations("secretsPresence");
    const { can } = useProjectContext();
    const [presence, setPresence] = useState<SecretPresence | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchPresence = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setPresence(await getSecretPresence(deploymentId));
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setLoading(false);
        }
    }, [deploymentId]);

    useEffect(() => {
        fetchPresence();
    }, [fetchPresence]);

    const defineUrl =
        can("app.viewSecretsLink") && presence?.vault_path
            ? vaultEditUrl(presence.vault_path)
            : null;
    const expected = [...(presence?.expected ?? [])].sort(
        (a, b) => Number(a.set) - Number(b.set) || a.name.localeCompare(b.name),
    );

    return (
        <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
                <div className="space-y-1.5">
                    <CardTitle className="flex items-center gap-2">
                        <KeyRound className="h-4 w-4" />
                        {t("title")}
                    </CardTitle>
                    <CardDescription>{t("description")}</CardDescription>
                </div>
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={fetchPresence}
                    disabled={loading}
                    aria-label={t("reload")}
                    title={t("reload")}
                >
                    <RefreshCw
                        className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"}
                    />
                </Button>
            </CardHeader>
            <CardContent className="space-y-4">
                {loading && !presence ? (
                    <Skeleton className="h-16 w-full" />
                ) : error ? (
                    <Alert variant="destructive">
                        <AlertDescription>
                            {t("loadFailed", { message: error })}
                        </AlertDescription>
                    </Alert>
                ) : presence ? (
                    <>
                        {expected.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                {t.rich("noneExpected", { code: renderCode })}
                            </p>
                        ) : (
                            <ul className="divide-y rounded-md border">
                                {expected.map((secret) => (
                                    <li
                                        key={secret.name}
                                        className="flex items-center justify-between gap-3 px-3 py-2"
                                    >
                                        <span className="font-mono text-sm">
                                            {secret.name}
                                        </span>
                                        <span className="flex items-center gap-3">
                                            {secret.set ? (
                                                <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                                    <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                                                    {t("set")}
                                                </span>
                                            ) : (
                                                <span className="flex items-center gap-1.5 text-sm font-medium text-red-600 dark:text-red-400">
                                                    <XCircle className="h-4 w-4" />
                                                    {t("missing")}
                                                </span>
                                            )}
                                            {!secret.set && defineUrl && (
                                                <a
                                                    href={defineUrl}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    title={t("defineHint", {
                                                        name: secret.name,
                                                    })}
                                                    className={buttonVariants({
                                                        variant: "outline",
                                                        size: "sm",
                                                    })}
                                                >
                                                    {t("define")}
                                                    <ExternalLink className="h-3.5 w-3.5" />
                                                </a>
                                            )}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        )}
                        {presence.extra.length > 0 && (
                            <div className="space-y-1.5">
                                <p className="text-xs font-medium text-muted-foreground">
                                    {t("extraTitle")}
                                </p>
                                <ul className="flex flex-wrap gap-2">
                                    {presence.extra.map((name) => (
                                        <li
                                            key={name}
                                            className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
                                        >
                                            {name}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </>
                ) : null}
            </CardContent>
        </Card>
    );
}
