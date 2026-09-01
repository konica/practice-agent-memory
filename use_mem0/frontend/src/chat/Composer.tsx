import { ArrowUp } from "lucide-react";
import { ComposerPrimitive } from "@assistant-ui/react";
import { Button } from "@/components/ui/button";

/**
 * A card-radius surface holding an auto-sizing textarea, not a default input.
 * Send disables itself while the composer is empty or a run is in flight, so
 * the button needs no state of its own.
 */
export function Composer() {
  return (
    <ComposerPrimitive.Root className="mx-auto flex w-full max-w-[var(--message-max-width)] items-end gap-2 rounded-xl border border-border bg-card p-2 transition-colors focus-within:border-ring">
      <ComposerPrimitive.Input
        rows={1}
        maxRows={8}
        placeholder="Message the assistant"
        aria-label="Message the assistant"
        className="flex-1 resize-none bg-transparent px-2 py-1.5 leading-[1.55] outline-none placeholder:text-text-tertiary"
      />
      <ComposerPrimitive.Send asChild>
        <Button size="icon" aria-label="Send message">
          <ArrowUp />
        </Button>
      </ComposerPrimitive.Send>
    </ComposerPrimitive.Root>
  );
}
