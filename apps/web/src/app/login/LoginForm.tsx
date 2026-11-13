"use client";

import { useActionState } from "react";
import { loginAction, mfaAction, type FormState } from "@/app/actions";
import { Button, Input, Label, Notice } from "@/components/ui/primitives";

export function LoginForm({ step, labels }: { step: "password" | "mfa"; labels: Record<string, string> }) {
  const [state, action, pending] = useActionState<FormState, FormData>(step === "mfa" ? mfaAction : loginAction, undefined);
  return (
    <form action={action} className="grid gap-4">
      {state?.error ? <Notice tone="error">{state.error}</Notice> : null}
      {step === "mfa" ? (
        <Label>
          {labels.code}
          <Input name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} required autoFocus />
        </Label>
      ) : (
        <>
          <Label>
            {labels.email}
            <Input name="email" type="email" autoComplete="username" required autoFocus maxLength={254} />
          </Label>
          <Label>
            {labels.password}
            <Input name="password" type="password" autoComplete="current-password" required maxLength={200} />
          </Label>
        </>
      )}
      <Button type="submit" disabled={pending}>{pending ? "…" : step === "mfa" ? labels.verify : labels.submit}</Button>
    </form>
  );
}
