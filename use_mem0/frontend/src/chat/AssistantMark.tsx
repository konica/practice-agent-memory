import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The assistant's identity mark: a small square at the avatar radius (7px),
 * not the pill the rest of the app uses. It sits beside settled assistant
 * messages and beside the typing indicator, so the assistant looks like the
 * same speaker whether it is streaming or done.
 */
export function AssistantMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-7 shrink-0 place-items-center rounded-[var(--radius-mark)] bg-primary text-primary-foreground",
        className,
      )}
    >
      <Sparkles className="size-3.5" />
    </span>
  );
}
