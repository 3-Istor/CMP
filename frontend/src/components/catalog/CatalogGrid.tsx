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
}

// Get backend URL from environment or default
const BACKEND_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace("/api", "") ||
  "http://localhost:8000";

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
              {t.image_path ? (
                <Image
                  src={`${BACKEND_URL}${t.image_path}`}
                  alt={`${t.name} icon`}
                  width={40}
                  height={40}
                  className="object-contain"
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
            <Button size="sm" className="w-full" onClick={() => handleClick(t)}>
              Deploy
            </Button>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
