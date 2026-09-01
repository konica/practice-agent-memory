import { Skeleton } from "@/components/ui/skeleton";
import { AssistantMark } from "./AssistantMark";

const BUBBLE = "bg-bubble-skeleton-bg";

/**
 * Shown while the history adapter's `load()` is in flight, which is a real
 * round trip on every conversation switch — around 450ms in practice — not a
 * delay invented to cover a gap. The placeholders take the bubble shapes so the
 * transcript does not jump when the real messages land.
 */
export function HistorySkeleton() {
  return (
    <div role="status" aria-label="Loading conversation" className="flex flex-col gap-6">
      <div className="flex justify-end">
        <Skeleton className={`h-11 w-56 rounded-[var(--radius-bubble-user)] ${BUBBLE}`} />
      </div>
      <div className="flex gap-3">
        <AssistantMark className="mt-0.5 opacity-40" />
        <Skeleton className={`h-24 w-full max-w-[85%] rounded-[var(--radius-bubble-assistant)] ${BUBBLE}`} />
      </div>
      <div className="flex justify-end">
        <Skeleton className={`h-11 w-40 rounded-[var(--radius-bubble-user)] ${BUBBLE}`} />
      </div>
    </div>
  );
}
