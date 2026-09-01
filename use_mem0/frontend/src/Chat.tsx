import { useMemo } from "react";
import { HttpAgent } from "@ag-ui/client";
import { AlertCircle } from "lucide-react";
import {
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { Button } from "@/components/ui/button";
import { makeHistoryAdapter } from "./historyAdapter";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// HttpAgent runs its own fetch, and a cross-origin fetch omits cookies unless
// asked. Without this the run reaches /agent with no session and the gate
// answers 401, even though every call in api.ts is authenticated.
const withCredentials = (url: string, init: RequestInit) =>
  fetch(url, { ...init, credentials: "include" });

// `@assistant-ui/react` ships no ready-made <Thread />; it exports primitives only.
// This is the smallest surface that exercises the runtime. Task 14 replaces it with
// the styled component from the assistant-ui registry.
function Message() {
  const role = useAuiState((state) => state.message.role);
  return (
    <MessagePrimitive.Root data-role={role}>
      <MessagePrimitive.Parts />
      <MessagePrimitive.Error>
        {/* The backend retries a transient model failure once and then lets it
            propagate (see the failure policy); this is the user-facing half of
            that path. Reload re-runs from the same user message rather than
            resuming the partial stream. */}
        <ErrorPrimitive.Root className="mt-2 flex items-start gap-2 rounded-md border border-error-border bg-error-bg px-3 py-2">
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
          <ErrorPrimitive.Message className="flex-1 text-[13px] text-error-text" />
          <ActionBarPrimitive.Reload asChild>
            <Button variant="ghost" size="sm" className="text-destructive">
              Retry
            </Button>
          </ActionBarPrimitive.Reload>
        </ErrorPrimitive.Root>
      </MessagePrimitive.Error>
    </MessagePrimitive.Root>
  );
}

export function Chat({ threadId }: { threadId: string }) {
  const agent = useMemo(
    () => new HttpAgent({ url: `${BASE}/agent`, threadId, fetch: withCredentials }),
    [threadId],
  );
  const runtime = useAgUiRuntime({
    agent,
    adapters: { history: makeHistoryAdapter(threadId) },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages>{() => <Message />}</ThreadPrimitive.Messages>
        </ThreadPrimitive.Viewport>
        <ComposerPrimitive.Root>
          <ComposerPrimitive.Input placeholder="Message the assistant" />
          <ComposerPrimitive.Send>Send</ComposerPrimitive.Send>
        </ComposerPrimitive.Root>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
