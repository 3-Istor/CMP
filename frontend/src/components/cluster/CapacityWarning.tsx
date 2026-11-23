import { type MoveCapacity } from "@/lib/api";
import { TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";

const GIB = 2 ** 30;

/** Non-blocking: only an estimated need over the room. A real one is refused by the start request itself. */
export function CapacityWarning({ capacity }: { capacity: MoveCapacity | null }) {
  const t = useTranslations("cluster");
  if (!capacity || !capacity.estimated || capacity.fits) return null;
  return (
    <p className="flex items-start gap-2 rounded-md border-l-[3px] border-amber-500 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-800 dark:text-amber-300">
      <TriangleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      {t("capacityEstimated", {
        cpu: capacity.need_cpu.toFixed(1),
        memory: (capacity.need_memory_bytes / GIB).toFixed(1),
        freeCpu: (capacity.free_cpu ?? 0).toFixed(1),
        freeMemory: ((capacity.free_memory_bytes ?? 0) / GIB).toFixed(1),
      })}
    </p>
  );
}
