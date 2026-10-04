// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { SessionState } from "@pickwise/api-client";
import { Alert, Button } from "@pickwise/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { useForm } from "react-hook-form";

import { AuthCard } from "@/components/auth-card";
import {
  NewPasswordFields,
  type NewPasswordValues,
  newPasswordSchema,
} from "@/components/password-fields";
import { api, call, errorMessage } from "@/lib/api";
import { routeForStage, SESSION_KEY } from "@/lib/session";

type Accept = (password: string | null) => Promise<void>;

function ChoosePassword({ accept }: { accept: Accept }) {
  const form = useForm<NewPasswordValues>({
    resolver: zodResolver(newPasswordSchema),
    defaultValues: { password: "", confirm: "" },
  });
  const { register, handleSubmit, formState } = form;
  return (
    <form
      onSubmit={handleSubmit(({ password }) => accept(password))}
      className="flex flex-col gap-4"
      noValidate
    >
      <NewPasswordFields register={register} errors={formState.errors} />
      <Button type="submit" disabled={formState.isSubmitting}>
        Set password and join
      </Button>
    </form>
  );
}

function JoinExisting({ accept }: { accept: Accept }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">You already have a Pickwise account. Accept to join.</p>
      <Button
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void accept(null).finally(() => setBusy(false));
        }}
      >
        Accept invitation
      </Button>
    </div>
  );
}

export default function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const invite = useQuery({
    queryKey: ["invite", token],
    queryFn: () =>
      call<{ valid: boolean; needs_password: boolean }>(
        api().GET("/v1/auth/invites/{token}", { params: { path: { token } } }),
      ),
  });

  const accept: Accept = async (password) => {
    setError(null);
    try {
      const session = await call<SessionState>(
        api().POST("/v1/auth/invites/accept", { body: { token, password } }),
      );
      queryClient.setQueryData(SESSION_KEY, session);
      router.replace(routeForStage(session.stage));
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  let body;
  if (invite.isLoading) {
    body = <p className="text-sm text-muted-foreground">Checking your invitation…</p>;
  } else if (invite.isError) {
    body = <Alert variant="destructive">{errorMessage(invite.error)}</Alert>;
  } else if (!invite.data?.valid) {
    body = (
      <>
        <Alert variant="destructive" data-testid="invite-invalid">
          This invitation is invalid or has expired. Ask your administrator to send a new one.
        </Alert>
        <Link href="/login" className="text-sm underline underline-offset-4">
          Go to sign in
        </Link>
      </>
    );
  } else {
    body = (
      <>
        {invite.data.needs_password ? (
          <ChoosePassword accept={accept} />
        ) : (
          <JoinExisting accept={accept} />
        )}
        {error ? <Alert variant="destructive">{error}</Alert> : null}
      </>
    );
  }

  return (
    <AuthCard title="Join your organisation" description="You've been invited to Pickwise.">
      {body}
    </AuthCard>
  );
}
