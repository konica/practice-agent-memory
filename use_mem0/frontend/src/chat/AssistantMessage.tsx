import { AuiIf, MessagePrimitive, type AssistantState } from "@assistant-ui/react";
import { AssistantMark } from "./AssistantMark";
import { MessageError } from "./MessageError";
import { TypingDots } from "./TypingIndicator";

// A turn that failed before producing a token has no parts to render, and an
// empty pill above the error banner reads as a reply the assistant never made.
const hasBody = (state: AssistantState) =>
  state.thread.isRunning ||
  state.message.parts.some((part) => part.type !== "text" || part.text !== "");

/**
 * The assistant's turn: mark, then bubble. The sharp 2px corner sits on the
 * left, mirroring the alignment side.
 */
export function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="flex w-full gap-3">
      <AssistantMark className="mt-0.5" />
      <div className="flex min-w-0 flex-1 flex-col items-start gap-2">
        <AuiIf condition={hasBody}>
          <div className="max-w-[85%] rounded-[var(--radius-bubble-assistant)] bg-secondary px-3.5 py-2.5 text-secondary-foreground">
            <MessagePrimitive.Parts>
              {({ part }) => {
                if (part.type !== "text") return null;
                // The primitive emits one synthetic empty running text part while
                // the message exists but has streamed nothing yet — that is the
                // streaming state, and it gets the dots inside this same bubble.
                if (part.status.type === "running" && part.text === "") {
                  return <TypingDots />;
                }
                return <p className="wrap-anywhere whitespace-pre-wrap">{part.text}</p>;
              }}
            </MessagePrimitive.Parts>
          </div>
        </AuiIf>

        <MessagePrimitive.Error>
          <MessageError />
        </MessagePrimitive.Error>
      </div>
    </MessagePrimitive.Root>
  );
}
