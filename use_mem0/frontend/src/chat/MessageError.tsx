import { AlertCircle } from "lucide-react";
import { ActionBarPrimitive, ErrorPrimitive } from "@assistant-ui/react";
import { Button } from "@/components/ui/button";

/**
 * The user-facing half of the failure policy: the backend retries a transient
 * model failure once and then lets it propagate. Retry re-runs from the same
 * user message rather than resuming the partial stream.
 *
 * The banner spends four separate tokens, not one destructive colour — surface,
 * border, and message text come from the error family; only the icon and the
 * Retry label are `--destructive`.
 */
export function MessageError() {
  return (
    <ErrorPrimitive.Root className="flex w-full items-start gap-2 rounded-md border border-error-border bg-error-bg px-3 py-2">
      <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
      <ErrorPrimitive.Message className="flex-1 text-[13px] text-error-text" />
      <ActionBarPrimitive.Reload asChild>
        <Button variant="ghost" size="sm" className="text-destructive hover:bg-destructive/10">
          Retry
        </Button>
      </ActionBarPrimitive.Reload>
    </ErrorPrimitive.Root>
  );
}
