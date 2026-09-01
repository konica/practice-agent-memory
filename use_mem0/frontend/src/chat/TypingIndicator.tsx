import { AssistantMark } from "./AssistantMark";

const DOT = "size-1.5 animate-bounce rounded-full bg-muted-foreground";

/** The dots on their own, for when an assistant bubble already surrounds them. */
export function TypingDots() {
  return (
    <span
      role="status"
      aria-label="The assistant is replying"
      className="flex h-[1.55em] items-center gap-1"
    >
      <span className={DOT} />
      <span className={`${DOT} [animation-delay:150ms]`} />
      <span className={`${DOT} [animation-delay:300ms]`} />
    </span>
  );
}

/**
 * The standalone indicator, laid out exactly like an assistant message — mark,
 * then bubble — so the row does not shift when the first token replaces it.
 */
export function TypingIndicator() {
  return (
    <div className="flex w-full gap-3">
      <AssistantMark className="mt-0.5" />
      <div className="rounded-[var(--radius-bubble-assistant)] bg-secondary px-3.5 py-2.5">
        <TypingDots />
      </div>
    </div>
  );
}
