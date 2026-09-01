import { ExportedMessageRepository } from "@assistant-ui/react";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import { getMessages } from "./api";

export function makeHistoryAdapter(threadId: string) {
  return {
    async load() {
      const { messages } = await getMessages(threadId);
      return ExportedMessageRepository.fromArray(fromAgUiMessages(messages));
    },
    async append() {
      /* The LangGraph checkpointer already persists every turn server-side. */
    },
  };
}
