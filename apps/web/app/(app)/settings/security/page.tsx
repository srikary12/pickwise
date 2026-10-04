// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  Input,
} from "@pickwise/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { NewPasswordFields, MIN_PASSWORD } from "@/components/password-fields";
import { QrCode } from "@/components/qr-code";
import { api, call, errorMessage } from "@/lib/api";
import { SESSION_KEY, useSession } from "@/lib/session";

const codeSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^\d{3}\s?\d{3}$/, "Enter the 6-digit code."),
});
type CodeValues = z.infer<typeof codeSchema>;

function CodeForm({
  label,
  submit,
  onDone,
  variant = "default",
}: {
  label: string;
  submit: (code: string) => Promise<void>;
  onDone?: () => void;
  variant?: "default" | "outline" | "destructive";
}) {
  const form = useForm<CodeValues>({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: "" },
  });
  const { register, handleSubmit, setError, reset, formState } = form;
  return (
    <form
      noValidate
      className="flex flex-wrap items-end gap-3"
      onSubmit={handleSubmit(async ({ code }) => {
        try {
          await submit(code);
          reset();
          onDone?.();
        } catch (error) {
          setError("root", { message: errorMessage(error) });
        }
      })}
    >
      <Field
        id={`code-${label}`}
        label="Authentication code"
        error={formState.errors.code?.message}
      >
        <Input
          id={`code-${label}`}
          inputMode="numeric"
          autoComplete="one-time-code"
          className="w-40"
          aria-invalid={Boolean(formState.errors.code)}
          {...register("code")}
        />
      </Field>
      <Button type="submit" variant={variant} disabled={formState.isSubmitting}>
        {label}
      </Button>
      {formState.errors.root ? (
        <Alert variant="destructive" className="w-full">
          {formState.errors.root.message}
        </Alert>
      ) : null}
    </form>
  );
}

function RecoveryCodes({ codes, onDismiss }: { codes: string[]; onDismiss: () => void }) {
  const text = codes.join("\n");
  return (
    <div className="flex flex-col gap-3" data-testid="recovery-codes-panel">
      <Alert variant="warning">
        Save these recovery codes somewhere safe. Each works once, and they won&apos;t be shown
        again.
      </Alert>
      <ul data-testid="recovery-codes" className="grid grid-cols-2 gap-2 font-mono text-sm">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="flex gap-3">
        <Button variant="outline" onClick={() => void navigator.clipboard.writeText(text)}>
          Copy
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            const url = URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain" }));
            const link = Object.assign(document.createElement("a"), {
              href: url,
              download: "pickwise-recovery-codes.txt",
            });
            link.click();
            URL.revokeObjectURL(url);
          }}
        >
          Download
        </Button>
        <Button onClick={onDismiss}>I&apos;ve saved them</Button>
      </div>
    </div>
  );
}

function MfaSection({ required }: { required: boolean }) {
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const enabled = session?.user.mfa_enabled ?? false;
  const refresh = () => queryClient.invalidateQueries({ queryKey: SESSION_KEY });

  async function begin() {
    setError(null);
    try {
      setSetup(await call(api().POST("/v1/auth/mfa/setup")));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Two-factor authentication
          <Badge variant={enabled ? "success" : "outline"} data-testid="mfa-status">
            {enabled ? "On" : "Off"}
          </Badge>
        </CardTitle>
        <CardDescription>
          Use an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, …).
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {required && !enabled ? (
          <Alert variant="warning" data-testid="mfa-required">
            Your organisation requires two-factor authentication. Set it up to continue.
          </Alert>
        ) : null}
        {error ? <Alert variant="destructive">{error}</Alert> : null}

        {codes ? (
          <RecoveryCodes
            codes={codes}
            onDismiss={() => {
              setCodes(null);
              void refresh();
            }}
          />
        ) : !enabled && !setup ? (
          <Button className="self-start" onClick={() => void begin()}>
            Set up two-factor authentication
          </Button>
        ) : !enabled && setup ? (
          <div className="flex flex-col gap-4">
            <ol className="list-decimal pl-5 text-sm">
              <li>Scan this QR code with your authenticator app.</li>
              <li>
                Or enter this key by hand:{" "}
                <code data-testid="mfa-secret" className="font-mono">
                  {setup.secret}
                </code>
              </li>
              <li>Enter the 6-digit code it shows.</li>
            </ol>
            <QrCode value={setup.otpauth_uri} label="QR code for your authenticator app" />
            <CodeForm
              label="Confirm"
              submit={async (code) => {
                const result = await call<{ recovery_codes: string[] }>(
                  api().POST("/v1/auth/mfa/confirm", { body: { code } }),
                );
                setSetup(null);
                setCodes(result.recovery_codes);
              }}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-6">
            <section className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">New recovery codes</h3>
              <p className="text-sm text-muted-foreground">Replaces your existing codes.</p>
              <CodeForm
                label="Generate"
                variant="outline"
                submit={async (code) => {
                  const result = await call<{ recovery_codes: string[] }>(
                    api().POST("/v1/auth/mfa/recovery-codes", { body: { code } }),
                  );
                  setCodes(result.recovery_codes);
                }}
              />
            </section>
            <section className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">Turn off</h3>
              <CodeForm
                label="Turn off"
                variant="destructive"
                submit={async (code) => {
                  await call(api().POST("/v1/auth/mfa/disable", { body: { code } }));
                }}
                onDone={() => void refresh()}
              />
            </section>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const passwordSchema = z
  .object({
    current: z.string().min(1, "Enter your current password."),
    password: z.string().min(MIN_PASSWORD, `Use at least ${MIN_PASSWORD} characters.`).max(256),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, {
    path: ["confirm"],
    message: "The passwords don't match.",
  });
type PasswordValues = z.infer<typeof passwordSchema>;

function PasswordSection() {
  const [done, setDone] = useState(false);
  const form = useForm<PasswordValues>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { current: "", password: "", confirm: "" },
  });
  const { register, handleSubmit, setError, reset, formState } = form;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>Changing it signs you out on your other devices.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          noValidate
          className="flex max-w-sm flex-col gap-4"
          onSubmit={handleSubmit(async (values) => {
            setDone(false);
            try {
              await call(
                api().POST("/v1/auth/password/change", {
                  body: { current_password: values.current, new_password: values.password },
                }),
              );
              reset();
              setDone(true);
            } catch (error) {
              setError("root", { message: errorMessage(error) });
            }
          })}
        >
          <Field id="current" label="Current password" error={formState.errors.current?.message}>
            <Input
              id="current"
              type="password"
              autoComplete="current-password"
              aria-invalid={Boolean(formState.errors.current)}
              {...register("current")}
            />
          </Field>
          <NewPasswordFields register={register} errors={formState.errors} />
          {formState.errors.root ? (
            <Alert variant="destructive">{formState.errors.root.message}</Alert>
          ) : null}
          {done ? <Alert variant="success">Your password was changed.</Alert> : null}
          <Button type="submit" className="self-start" disabled={formState.isSubmitting}>
            Change password
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function SecurityPage() {
  const required = useSearchParams().get("required") === "1";
  const { data: session } = useSession();
  if (!session) return null;
  return (
    <div className="flex max-w-2xl flex-col gap-6">
      <h1 className="text-2xl font-semibold">Security</h1>
      <MfaSection required={required || session.stage === "mfa_enrolment_required"} />
      {session.stage === "ready" ? <PasswordSection /> : null}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <SecurityPage />
    </Suspense>
  );
}
