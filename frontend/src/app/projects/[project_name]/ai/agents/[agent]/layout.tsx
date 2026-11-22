"use client";

import { AgentFrame } from "@/components/ai/AgentFrame";
import { useParams } from "next/navigation";

export default function AgentLayout({ children }: { children: React.ReactNode }) {
  const params = useParams();
  return <AgentFrame agentName={decodeURIComponent(params.agent as string)}>{children}</AgentFrame>;
}
