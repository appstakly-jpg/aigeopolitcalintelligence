"use client";

import { useActionState, type ReactNode } from "react";
import { Button, Notice } from "@/components/ui/primitives";

type State = { error?: string; ok?: string } | undefined;

/** Small wrapper for server-action forms: shows success/error and disables while pending. */
export function ActionForm({ action, children, submit, variant = "default", className, confirm }: {
  action: (prev: State, form: FormData) => Promise<State>;
  children?: ReactNode;
  submit: ReactNode;
  variant?: "default" | "outline" | "destructive" | "secondary";
  className?: string;
  confirm?: string;
}) {
  const [state, formAction, pending] = useActionState<State, FormData>(action, undefined);
  return (
    <form action={formAction} className={className ?? "grid gap-2"}
      onSubmit={(e) => {
        if (confirm && !window.confirm(confirm)) e.preventDefault();
      }}>
      {children}
      <div className="flex items-center gap-2">
        <Button type="submit" variant={variant} size="sm" disabled={pending}>{pending ? "Working…" : submit}</Button>
      </div>
      {state?.error ? <Notice tone="error">{state.error}</Notice> : null}
      {state?.ok ? <Notice tone="ok">{state.ok}</Notice> : null}
    </form>
  );
}
