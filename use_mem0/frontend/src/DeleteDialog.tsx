import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// shadcn's Dialog rather than a hand-rolled overlay: it brings focus trapping,
// Escape handling and the right ARIA roles, which matter here because this
// dialog is the guard on an irreversible action. The copy promises permanence,
// so there is deliberately no undo affordance anywhere in this flow.
export function DeleteDialog({
  open,
  title,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      {/* DialogContent's own sm:max-w-sm wins over an unprefixed max-w, so the
          override has to carry the same breakpoint prefix. */}
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle className="text-base">Delete “{title}”?</DialogTitle>
          <DialogDescription className="text-[13.5px]">
            This removes the conversation and its messages. Anything Assistant Agent has
            already learned from it stays remembered — deleting a conversation doesn’t erase
            that memory.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
