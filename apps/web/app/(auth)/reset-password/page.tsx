// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Alert, Button } from "@pickwise/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";

import { AuthCard } from "@/components/auth-card";
import {
  NewPasswordFields,
  type NewPasswordValues,
  newPasswordSchema,
} from "@/components/password-fields";
import { api, call, errorMessage } from "@/lib/api";

function ResetForm() {
  const token = useSearchParams().get("token") ?? "";
  const [done, setDone] = useState(false);
  const form = useForm<NewPasswordValues>({
    resolver: zodResolver(newPasswordSchema),
    defaultValues: { password: "", confirm: "" },
  });
  const { register, handleSubmit, setError, formState } = form;

  async function onSubmit({ password }: NewPasswordValues) {
    try {
      await call(api().POST("/v1/auth/password/reset", { body: { token, password } }));
      setDone(true);
    } catch (error) {
      setError("root", { message: errorMessage(error) });
    }
  }

  if (!token) return <Alert variant="destructive">This reset link is incomplete.</Alert>;
  if (done) {
    return (
      <>
        <Alert variant="success" data-testid="password-reset-done">
          Your password was changed. You&apos;ve been signed out everywhere.
        </Alert>
        <Link href="/login" className="text-sm underline underline-offset-4">
          Sign in
        </Link>
      </>
    );
  }
  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
      <NewPasswordFields register={register} errors={formState.errors} />
      {formState.errors.root ? (
        <Alert variant="destructive">{formState.errors.root.message}</Alert>
      ) : null}
      <Button type="submit" disabled={formState.isSubmitting}>
        Change password
      </Button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <AuthCard title="Choose a new password">
      <Suspense>
        <ResetForm />
      </Suspense>
    </AuthCard>
  );
}
