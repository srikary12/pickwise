// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Alert, Button, Field, Input } from "@pickwise/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/auth-card";
import { api, call, errorMessage } from "@/lib/api";

const schema = z.object({ email: z.email("Enter a valid email address.") });
type Values = z.infer<typeof schema>;

export default function ForgotPasswordPage() {
  const [sent, setSent] = useState(false);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "" } });
  const { register, handleSubmit, setError, formState } = form;

  async function onSubmit(values: Values) {
    try {
      await call(api().POST("/v1/auth/password/forgot", { body: values }));
      setSent(true);
    } catch (error) {
      setError("root", { message: errorMessage(error) });
    }
  }

  return (
    <AuthCard title="Reset your password" description="We'll email you a link that works once.">
      {sent ? (
        <Alert variant="success" data-testid="reset-requested">
          If that address has an account, we&apos;ve sent a reset link. It expires in 30 minutes.
        </Alert>
      ) : (
        <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
          <Field id="email" label="Email" error={formState.errors.email?.message}>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              aria-invalid={Boolean(formState.errors.email)}
              {...register("email")}
            />
          </Field>
          {formState.errors.root ? (
            <Alert variant="destructive">{formState.errors.root.message}</Alert>
          ) : null}
          <Button type="submit" disabled={formState.isSubmitting}>
            Send reset link
          </Button>
        </form>
      )}
      <Link href="/login" className="text-sm underline underline-offset-4">
        Back to sign in
      </Link>
    </AuthCard>
  );
}
