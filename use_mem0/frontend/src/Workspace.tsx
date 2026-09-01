import { useCallback, useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  createConversation,
  listConversations,
  logout,
  type Conversation,
  type User,
} from "./api";
import { Chat } from "./chat/Chat";
import { ConversationList } from "./ConversationList";

export function Workspace({ user }: { user: User }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const rows = await listConversations();
    setConversations(rows);
    setActiveId((current) =>
      current && rows.some((row) => row.id === current) ? current : (rows[0]?.id ?? null),
    );
  }, []);

  useEffect(() => {
    // refresh only sets state after awaiting the network; the rule cannot see
    // past the await.
    // oxlint-disable-next-line react/set-state-in-effect
    void refresh();
  }, [refresh]);

  const startNewChat = async () => {
    const created = await createConversation();
    await refresh();
    setActiveId(created.id);
  };

  return (
    <div className="flex h-screen bg-background">
      <aside className="flex w-[var(--sidebar-width)] flex-col border-r border-border bg-secondary p-2.5">
        <Button variant="ghost" onClick={startNewChat} className="mb-2.5 justify-start gap-2">
          <Plus />
          New chat
        </Button>

        {/* Radix wraps the viewport's content in a display:table div, which sizes
            to its widest child — a long title would then widen the row past the
            sidebar instead of ellipsing inside it. Force that wrapper to block. */}
        <ScrollArea className="flex-1 [&>[data-slot=scroll-area-viewport]>div]:!block">
          <ConversationList
            conversations={conversations}
            activeId={activeId}
            onSelect={setActiveId}
            onChanged={refresh}
          />
        </ScrollArea>

        <div className="mt-2.5 flex items-center gap-2 border-t border-border pt-2.5">
          <Avatar className="size-6 rounded-[var(--radius-mark)] after:rounded-[var(--radius-mark)]">
            {user.picture && <AvatarImage src={user.picture} alt="" className="rounded-[var(--radius-mark)]" />}
            <AvatarFallback className="rounded-[var(--radius-mark)] bg-[var(--avatar-placeholder-bg)] text-[10px]">
              {(user.name ?? user.email).slice(0, 1).toUpperCase()}
            </AvatarFallback>
          </Avatar>
          <span className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--text-account)]">
            {user.name ?? user.email}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              await logout();
              window.location.reload();
            }}
          >
            Sign out
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-hidden">
        {/* key={activeId} forces a fresh runtime per conversation, so the history
            adapter re-runs on switch instead of reusing the previous thread's state. */}
        {activeId ? (
          <Chat key={activeId} threadId={activeId} />
        ) : (
          <div className="grid h-full place-items-center p-10">
            <p className="max-w-md text-center text-muted-foreground">
              You don’t have any conversations yet. Start one below, and I’ll remember what
              matters as we go — in this conversation and any you start later.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
