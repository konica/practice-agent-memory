import { AuiIf, ThreadPrimitive, type AssistantState } from "@assistant-ui/react";
import { AssistantMessage } from "./AssistantMessage";
import { Composer } from "./Composer";
import { EmptyState } from "./EmptyState";
import { HistorySkeleton } from "./HistorySkeleton";
import { TypingIndicator } from "./TypingIndicator";
import { UserMessage } from "./UserMessage";

const isEmpty = (state: AssistantState) => state.thread.isEmpty;

// Between "send" and the first assistant token there is no assistant message to
// hang the dots on, so the thread renders the indicator itself. Once the message
// exists, <AssistantMessage> takes over and this turns off — the two never
// overlap.
const isAwaitingReply = (state: AssistantState) =>
  state.thread.isRunning && state.thread.messages.at(-1)?.role !== "assistant";

export function Thread({ isLoadingHistory }: { isLoadingHistory: boolean }) {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col bg-background">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-6 py-8">
        <div className="mx-auto flex w-full max-w-[var(--message-max-width)] flex-col gap-6">
          {isLoadingHistory ? (
            <HistorySkeleton />
          ) : (
            <>
              <AuiIf condition={isEmpty}>
                <EmptyState />
              </AuiIf>

              <ThreadPrimitive.Messages>
                {({ message }) =>
                  message.role === "user" ? <UserMessage /> : <AssistantMessage />
                }
              </ThreadPrimitive.Messages>

              <AuiIf condition={isAwaitingReply}>
                <TypingIndicator />
              </AuiIf>
            </>
          )}
        </div>
      </ThreadPrimitive.Viewport>

      <div className="border-t border-border px-6 py-4">
        <Composer />
      </div>
    </ThreadPrimitive.Root>
  );
}
