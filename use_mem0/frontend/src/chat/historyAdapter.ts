import { useMemo, useRef, useState } from "react";
import { ExportedMessageRepository } from "@assistant-ui/react";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import { getMessages } from "../api";

// A floor, not a delay: the skeleton goes up when the request goes out and
// stays up until the request comes back, so it always has a real load behind
// it. The floor only stops a fast round trip from flashing it for two frames,
// which reads as a glitch rather than as loading.
const MIN_SKELETON_MS = 450;

/**
 * The thread's history adapter, plus whether its `load()` is in flight.
 *
 * Switching conversations costs a round trip to /conversations/{id}/messages
 * whether or not rehydration succeeds, and that is the latency the skeleton
 * reports. `isLoadingHistory` starts true because `<Chat>` is keyed by thread
 * id, so a mount *is* a conversation switch and `load()` always follows it.
 */
export function useHistoryAdapter(threadId: string) {
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  // A late floor timer from a previous conversation must not clear the
  // skeleton belonging to the one now on screen.
  const loadCount = useRef(0);

  const adapter = useMemo(
    () => ({
      async load() {
        const thisLoad = ++loadCount.current;
        const startedAt = Date.now();
        setIsLoadingHistory(true);
        try {
          const { messages } = await getMessages(threadId);
          return ExportedMessageRepository.fromArray(fromAgUiMessages(messages));
        } finally {
          const settle = () => {
            if (loadCount.current === thisLoad) setIsLoadingHistory(false);
          };
          const remaining = MIN_SKELETON_MS - (Date.now() - startedAt);
          if (remaining > 0) setTimeout(settle, remaining);
          else settle();
        }
      },
      async append() {
        /* The LangGraph checkpointer already persists every turn server-side. */
      },
    }),
    [threadId],
  );

  return { adapter, isLoadingHistory };
}
