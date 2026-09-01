import { useEffect, useRef, useState } from "react";
import { MoreVertical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { deleteConversation, renameConversation, type Conversation } from "./api";
import { DeleteDialog } from "./DeleteDialog";

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onChanged,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Escape unmounts the input, and an unmount can still deliver a blur — which
  // would commit the very draft Escape just discarded. The flag makes "cancel"
  // survive whichever of the two events lands last.
  const cancelledRef = useRef(false);
  // Radix returns focus to the ⋮ trigger when its menu closes. Choosing Rename
  // opens the inline field, so that restore has to be suppressed for this one
  // close, or the field loses focus the instant it appears.
  const keepFocusRef = useRef(false);

  useEffect(() => {
    if (!renamingId) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [renamingId]);

  const startRename = (conversation: Conversation, label: string) => {
    keepFocusRef.current = true;
    cancelledRef.current = false;
    setDraft(label);
    setRenamingId(conversation.id);
  };

  const cancelRename = () => {
    cancelledRef.current = true;
    setRenamingId(null);
  };

  const commitRename = async (id: string) => {
    if (cancelledRef.current) {
      cancelledRef.current = false;
      return;
    }
    const title = draft.trim();
    setRenamingId(null);
    if (title) {
      await renameConversation(id, title);
      onChanged();
    }
  };

  return (
    <>
      <nav className="flex flex-col gap-0.5">
        {conversations.map((conversation) => {
          const isActive = conversation.id === activeId;
          // A conversation is titled by its first message, so an untitled one is
          // one nobody has spoken in yet.
          const label = conversation.title ?? "New chat";
          const isRenaming = renamingId === conversation.id;
          return (
            <div
              key={conversation.id}
              className={`group relative rounded-md ${isActive ? "bg-accent" : "hover:bg-accent/50"}`}
            >
              {isRenaming ? (
                <div className="p-1">
                  <Input
                    ref={inputRef}
                    value={draft}
                    aria-label="Conversation title"
                    onChange={(event) => setDraft(event.target.value)}
                    onBlur={() => commitRename(conversation.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") commitRename(conversation.id);
                      if (event.key === "Escape") cancelRename();
                    }}
                    className="h-7 rounded-[var(--radius-input)] px-2 text-[13.5px]"
                  />
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => onSelect(conversation.id)}
                    aria-current={isActive ? "page" : undefined}
                    // Truncation is CSS, never JS: the backend's 50-character
                    // title cap and what fits in 260px are unrelated limits.
                    className="block w-full truncate rounded-md py-1.5 pl-2.5 pr-8 text-left text-[13.5px]"
                  >
                    {label}
                  </button>

                  {/* Absolutely positioned rather than nested in the row button:
                      a button inside a button is invalid, and the row has to stay
                      keyboard-reachable. */}
                  <div className="absolute inset-y-0 right-1 flex items-center">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label="Conversation actions"
                          className="opacity-0 focus-visible:opacity-100 group-hover:opacity-100 aria-expanded:opacity-100"
                        >
                          <MoreVertical />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent
                        align="end"
                        onCloseAutoFocus={(event) => {
                          if (!keepFocusRef.current) return;
                          keepFocusRef.current = false;
                          event.preventDefault();
                        }}
                      >
                        <DropdownMenuItem onSelect={() => startRename(conversation, label)}>
                          Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          onSelect={() => setPendingDelete(conversation)}
                        >
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </nav>

      <DeleteDialog
        open={pendingDelete !== null}
        title={pendingDelete?.title ?? "New chat"}
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          const target = pendingDelete!;
          setPendingDelete(null);
          await deleteConversation(target.id);
          onChanged();
        }}
      />
    </>
  );
}
