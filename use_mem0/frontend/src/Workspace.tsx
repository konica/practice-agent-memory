import { useEffect, useRef, useState } from "react";
import { createConversation, listConversations, type Conversation, type User } from "./api";
import { Chat } from "./Chat";

// One hardcoded conversation; the real sidebar arrives in Task 13.
export function Workspace(_props: { user: User }) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  // StrictMode runs effects twice in development. Without this guard the second
  // pass finds the list still empty and creates a second, orphaned conversation.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    listConversations().then(async (existing) => {
      setConversation(existing[0] ?? (await createConversation()));
    });
  }, []);

  if (!conversation) return null;
  return <Chat threadId={conversation.id} />;
}
