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

interface Props {
  templates: CatalogTemplate[];
  onDeploy: (template: CatalogTemplate) => void;
}

export function CatalogGrid({ templates, onDeploy }: Props) {
  const handleClick = (t: CatalogTemplate) => {
    console.log("Button clicked for:", t.name);
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
              <span className="text-3xl">{t.icon}</span>
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
              <span className="font-medium">Provisions:</span> 2 OpenStack VMs +
              2 AWS instances
            </div>
            <Button size="sm" className="w-full" onClick={() => handleClick(t)}>
              Deploy
            </Button>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
