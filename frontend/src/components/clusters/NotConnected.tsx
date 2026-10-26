export function NotConnected({ cloud }: { cloud: string }) {
  return (
    <div className="rounded-lg border border-dashed px-4 py-6 text-sm">
      <p className="font-medium">Cluster {cloud.toUpperCase()} non connecté</p>
      <p className="text-muted-foreground">
        Les données de ce projet reviennent quand le cluster est rallumé.
      </p>
    </div>
  );
}
