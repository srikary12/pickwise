// SPDX-License-Identifier: AGPL-3.0-only
import { Field, Input } from "@pickwise/ui";
import type { FieldErrors, Path, UseFormRegister } from "react-hook-form";
import { z } from "zod";

export const MIN_PASSWORD = 12;

/** Chosen-password rules, mirrored from the API (which stays the authority). */
export const newPasswordSchema = z
  .object({
    password: z.string().min(MIN_PASSWORD, `Use at least ${MIN_PASSWORD} characters.`).max(256),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, {
    path: ["confirm"],
    message: "The passwords don't match.",
  });
export type NewPasswordValues = z.infer<typeof newPasswordSchema>;

/** The new-password + confirm pair, usable in any form whose values include both fields. */
export function NewPasswordFields<T extends NewPasswordValues>({
  register,
  errors,
}: {
  register: UseFormRegister<T>;
  errors: FieldErrors<T>;
}) {
  const passwordError = errors.password?.message as string | undefined;
  const confirmError = errors.confirm?.message as string | undefined;
  return (
    <>
      <Field
        id="password"
        label="New password"
        hint={`At least ${MIN_PASSWORD} characters.`}
        error={passwordError}
      >
        <Input
          id="password"
          type="password"
          autoComplete="new-password"
          aria-invalid={Boolean(passwordError)}
          {...register("password" as Path<T>)}
        />
      </Field>
      <Field id="confirm" label="Confirm password" error={confirmError}>
        <Input
          id="confirm"
          type="password"
          autoComplete="new-password"
          aria-invalid={Boolean(confirmError)}
          {...register("confirm" as Path<T>)}
        />
      </Field>
    </>
  );
}
