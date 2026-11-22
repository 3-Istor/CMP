import { redirect } from "next/navigation";

export default async function ProjectAssistantsPage({
  params,
}: {
  params: Promise<{ project_name: string }>;
}) {
  const { project_name } = await params;
  redirect(`/projects/${encodeURIComponent(project_name)}/ai`);
}
