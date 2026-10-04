// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { SessionState } from "@pickwise/api-client";
import { Alert, Button, buttonVariants, Field, Input } from "@pickwise/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/auth-card";
import { useGuest } from "@/components/guards";
import { api, call, errorMessage } from "@/lib/api";
import { routeForStage, SESSION_KEY } from "@/lib/session";

const schema = z.object({
  email: z.email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});
type Values = z.infer<typeof schema>;

export default function LoginPage() {
  useGuest();
  const router = useRouter();
  const queryClient = useQueryClient();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });
  const { register, handleSubmit, control, setError, formState } = form;

  // Which single sign-on options cover this email's domain (ids only).
  const email = useWatch({ control, name: "email" });
  const valid = z.email().safeParse(email).success;
  const sso = useQuery({
    queryKey: ["sso-discover", email],
    enabled: valid,
    queryFn: () =>
      call<{ options: { tenant_id: string; sso_config_id: string; enforced: boolean }[] }>(
        api().GET("/v1/auth/sso/discover", { params: { query: { email } } }),
      ),
  });
  const options = sso.data?.options ?? [];
  const enforced = options.some((o) => o.enforced);

  async function onSubmit(values: Values) {
    try {
      const session = await call<SessionState>(api().POST("/v1/auth/login", { body: values }));
      queryClient.setQueryData(SESSION_KEY, session);
      router.replace(routeForStage(session.stage));
    } catch (error) {
      setError("root", { message: errorMessage(error) });
    }
  }

  return (
    <AuthCard title="Sign in" description="Use your work email.">
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
        {options.map((option, index) => {
          const query = new URLSearchParams({
            tenant_id: option.tenant_id,
            sso_config_id: option.sso_config_id,
            login_hint: email,
          });
          return (
            // A plain link: the API answers with a redirect to the identity provider.
            <a
              key={option.sso_config_id}
              href={`/api/v1/auth/sso/start?${query}`}
              className={buttonVariants({ variant: "outline" })}
            >
              Continue with single sign-on{options.length > 1 ? ` (${index + 1})` : ""}
            </a>
          );
        })}
        {enforced ? (
          <Alert>Your organisation requires single sign-on.</Alert>
        ) : (
          <>
            <Field id="password" label="Password" error={formState.errors.password?.message}>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                aria-invalid={Boolean(formState.errors.password)}
                {...register("password")}
              />
            </Field>
            {formState.errors.root ? (
              <Alert variant="destructive">{formState.errors.root.message}</Alert>
            ) : null}
            <Button type="submit" disabled={formState.isSubmitting}>
              {formState.isSubmitting ? "Signing in…" : "Sign in"}
            </Button>
          </>
        )}
      </form>
      <p className="text-sm">
        <Link href="/forgot-password" className="underline underline-offset-4">
          Forgot your password?
        </Link>
      </p>
    </AuthCard>
  );
}
