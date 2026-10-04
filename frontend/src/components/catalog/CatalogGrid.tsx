"use client";

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
  const handleClick = (t: CatalogTemplate) => {
    if (!canDeploy) return;
    onDeploy(t);
  };

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {templates.map((t) => (
        <Card
          key={t.id}
          className="flex flex-col hover:shadow-md transition-shadow"
        >
          <CardHeader className="pb-2">
            <div className="flex items-start justify-between">
              {t.image_path ? (
                <Image
                  src={t.image_path}
                  alt={`${t.name} icon`}
                  width={40}
                  height={40}
                  className="object-contain"
                  // style={{ width: "auto", height: "auto" }}
                  unoptimized
                />
              ) : (
                <span className="text-3xl">{t.icon}</span>
              )}
              <Badge variant="outline" className="text-xs">
                {t.category}
              </Badge>
            </div>
            <CardTitle className="text-base">{t.name}</CardTitle>
            <CardDescription className="text-xs line-clamp-2">
              {t.description}
            </CardDescription>
          </CardHeader>
          <CardContent className="mt-auto pt-0">
            <div className="mb-3 text-xs text-muted-foreground">
              <span className="font-medium">Template:</span> {t.id}
            </div>
            {canDeploy && (
              <Button
                size="sm"
                className="w-full"
                onClick={() => handleClick(t)}
              >
                Deploy
              </Button>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
