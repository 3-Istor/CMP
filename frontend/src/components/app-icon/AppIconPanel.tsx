"use client";

import { useAppContext } from "@/components/projects/AppContext";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { AppIcon } from "./AppIcon";
import { AppIconDialog } from "./AppIconDialog";

export function AppIconPanel() {
  const t = useTranslations("appIcon");
  const { deployment, refresh } = useAppContext();
  const { can } = useProjectContext();
  const [open, setOpen] = useState(false);

  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 pt-6">
        <div className="flex items-center gap-3">
          <AppIcon
            appId={deployment.id}
            templateIcon={deployment.template_icon}
            icon={deployment.icon}
            className="h-9 w-9"
          />
          <div>
            <h2 className="text-sm font-medium">{t("panel.title")}</h2>
            <p className="text-xs text-muted-foreground">
              {t("panel.description")}
            </p>
          </div>
        </div>
        {can("app.editIcon") && (
          <Button variant="outline" onClick={() => setOpen(true)}>
            {t("menu")}
          </Button>
        )}
        <AppIconDialog
          deployment={deployment}
          open={open}
          onOpenChange={setOpen}
          onSaved={() => void refresh()}
        />
      </CardContent>
    </Card>
  );
}
