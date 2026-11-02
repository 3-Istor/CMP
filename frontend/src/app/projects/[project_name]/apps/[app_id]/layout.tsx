"use client";

import { AppProvider } from "@/components/projects/AppContext";
import { AppFrame } from "@/components/projects/AppFrame";
import { useParams } from "next/navigation";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const params = useParams();
  return (
    <AppProvider
      projectName={params.project_name as string}
      appId={Number(params.app_id)}
    >
      <AppFrame>{children}</AppFrame>
    </AppProvider>
  );
}
