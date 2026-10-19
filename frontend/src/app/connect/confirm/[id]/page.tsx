import { ConfirmationPanel } from "@/components/agents/ConfirmationPanel";

export default async function ConfirmPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <ConfirmationPanel confirmationId={id} />
    </main>
  );
}
