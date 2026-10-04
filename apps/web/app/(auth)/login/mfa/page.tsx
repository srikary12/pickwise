// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { SessionState } from "@pickwise/api-client";
import { Alert, Button, Field, Input } from "@pickwise/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/auth-card";
import { useStageGuard } from "@/components/guards";
import { api, call, errorMessage } from "@/lib/api";
import { routeForStage, SESSION_KEY } from "@/lib/session";

const schema = z.object({ code: z.string().trim().min(1, "Enter the code.") });
type Values = z.infer<typeof schema>;

export default function MfaPage() {
  useStageGuard("mfa_pending");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [recovery, setRecovery] = useState(false);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { code: "" } });
  const { register, handleSubmit, setError, reset, formState } = form;

  async function onSubmit({ code }: Values) {
    try {
      const body = recovery ? { recovery_code: code } : { code };
      const session = await call<SessionState>(api().POST("/v1/auth/mfa/verify", { body }));
      queryClient.setQueryData(SESSION_KEY, session);
      router.replace(routeForStage(session.stage));
    } catch (error) {
      setError("root", { message: errorMessage(error) });
    }
  }

  return (
    <AuthCard
      title="Two-factor authentication"
      description={
        recovery
          ? "Enter one of your recovery codes. Each works once."
          : "Enter the 6-digit code from your authenticator app."
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
        <Field
          id="code"
          label={recovery ? "Recovery code" : "Authentication code"}
          error={formState.errors.code?.message}
        >
          <Input
            id="code"
            autoComplete="one-time-code"
            inputMode={recovery ? "text" : "numeric"}
            autoFocus
            aria-invalid={Boolean(formState.errors.code)}
            {...register("code")}
          />
        </Field>
        {formState.errors.root ? (
          <Alert variant="destructive">{formState.errors.root.message}</Alert>
        ) : null}
        <Button type="submit" disabled={formState.isSubmitting}>
          Verify
        </Button>
      </form>
      <Button
        variant="link"
        className="self-start px-0"
        onClick={() => {
          setRecovery(!recovery);
          reset();
        }}
      >
        {recovery ? "Use an authenticator code instead" : "Use a recovery code instead"}
      </Button>
    </AuthCard>
  );
}
