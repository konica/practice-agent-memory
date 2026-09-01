import { MessagePrimitive } from "@assistant-ui/react";

/**
 * The user's turn: right-aligned, and carrying no mark — only the assistant
 * needs to identify itself. The sharp 2px corner is the tail, so it mirrors the
 * side the bubble aligns to.
 */
export function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex w-full justify-end">
      <div className="max-w-[85%] rounded-[var(--radius-bubble-user)] bg-bubble-user-bg px-3.5 py-2.5 text-foreground">
        <MessagePrimitive.Parts>
          {({ part }) =>
            part.type === "text" ? (
              <p className="wrap-anywhere whitespace-pre-wrap">{part.text}</p>
            ) : null
          }
        </MessagePrimitive.Parts>
      </div>
    </MessagePrimitive.Root>
  );
}
