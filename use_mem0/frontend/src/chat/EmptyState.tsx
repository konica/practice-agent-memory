import { AssistantMark } from "./AssistantMark";

/** A conversation that exists but nobody has spoken in yet. */
export function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 py-20 text-center">
      <AssistantMark />
      <p className="max-w-sm text-muted-foreground">
        Ask me anything. I’ll remember what matters as we go — in this conversation and any
        you start later.
      </p>
    </div>
  );
}
