import { useMemo } from "react";
import { HttpAgent } from "@ag-ui/client";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { useHistoryAdapter } from "./historyAdapter";
import { Thread } from "./Thread";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// HttpAgent runs its own fetch, and a cross-origin fetch omits cookies unless
// asked. Without this the run reaches /agent with no session and the gate
// answers 401, even though every call in api.ts is authenticated.
const withCredentials = (url: string, init: RequestInit) =>
  fetch(url, { ...init, credentials: "include" });

export function Chat({ threadId }: { threadId: string }) {
  const agent = useMemo(
    () => new HttpAgent({ url: `${BASE}/agent`, threadId, fetch: withCredentials }),
    [threadId],
  );
  const { adapter, isLoadingHistory } = useHistoryAdapter(threadId);
  const runtime = useAgUiRuntime({ agent, adapters: { history: adapter } });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread isLoadingHistory={isLoadingHistory} />
    </AssistantRuntimeProvider>
  );
}
