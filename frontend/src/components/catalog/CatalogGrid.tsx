"use client";
import { TemplateIcon } from "./TemplateIcon";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { CatalogTemplate } from "@/types";
import Image from "next/image";
import { useTranslations } from "next-intl";

interface Props {
  templates: CatalogTemplate[];
  onDeploy: (template: CatalogTemplate) => void;
  /**
   * Whether the viewer may deploy. Defaults to true so callers that render the
   * catalogue outside a project context are unaffected.
   */
  canDeploy?: boolean;
}

export function CatalogGrid({ templates, onDeploy, canDeploy = true }: Props) {
  const t = useTranslations("catalog");

  const handleClick = (template: CatalogTemplate) => {
    if (!canDeploy) return;
    onDeploy(template);
  };

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {templates.map((template) => (
        <Card
          key={template.id}
          className="flex flex-col hover:shadow-md transition-shadow"
        >
          <CardHeader className="pb-2">
            <div className="flex items-start justify-between">
              {template.image_path ? (
                <Image
                  src={template.image_path}
                  alt={t("iconAlt", { name: template.name })}
                  width={40}
                  height={40}
                  className="object-contain"
                  // style={{ width: "auto", height: "auto" }}
                  unoptimized
                />
              ) : (
                <TemplateIcon icon={template.icon} />
              )}
              <Badge variant="outline" className="text-xs">
                {template.category}
              </Badge>
            </div>
            <CardTitle className="text-base">{template.name}</CardTitle>
            <CardDescription className="text-xs line-clamp-2">
              {template.description}
            </CardDescription>
          </CardHeader>
          <CardContent className="mt-auto pt-0">
            <div className="mb-3 text-xs text-muted-foreground">
              <span className="font-medium">{t("template")}</span> {template.id}
            </div>
            {canDeploy && (
              <Button
                size="sm"
                className="w-full"
                onClick={() => handleClick(template)}
              >
                {t("deploy")}
              </Button>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
