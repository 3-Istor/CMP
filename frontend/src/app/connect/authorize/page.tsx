import { ConsentPanel } from "@/components/agents/ConsentPanel";

export default async function AuthorizePage({
  searchParams,
}: {
  searchParams: Promise<{ request?: string }>;
}) {
  const { request } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <ConsentPanel requestId={request ?? ""} />
    </main>
  );
}
