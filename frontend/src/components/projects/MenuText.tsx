export function MenuText({ label, reason }: { label: string; reason: string | null }) {
  return (
    <span className="flex flex-col">
      <span>{label}</span>
      {reason && (
        <span className="text-xs font-normal text-muted-foreground">
          {reason}
        </span>
      )}
    </span>
  );
}
