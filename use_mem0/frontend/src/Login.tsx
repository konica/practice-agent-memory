import { Button } from "@/components/ui/button";
import { loginUrl } from "./api";

export function Login() {
  return (
    <div className="grid min-h-screen place-items-center bg-background">
      <div className="max-w-sm text-center">
        <h1 className="mb-2 text-[22px] font-medium text-foreground">Assistant Agent</h1>
        <p className="mb-7 text-muted-foreground">
          A personal assistant that remembers what matters across your conversations.
        </p>
        <Button asChild>
          <a href={loginUrl}>Sign in with Google</a>
        </Button>
      </div>
    </div>
  );
}
