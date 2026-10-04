// SPDX-License-Identifier: AGPL-3.0-only
import { Field, Input } from "@pickwise/ui";
import type { FieldErrors, UseFormRegister } from "react-hook-form";
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

export function NewPasswordFields({
  register,
  errors,
}: {
  register: UseFormRegister<NewPasswordValues>;
  errors: FieldErrors<NewPasswordValues>;
}) {
  return (
    <>
      <Field
        id="password"
        label="New password"
        hint={`At least ${MIN_PASSWORD} characters.`}
        error={errors.password?.message}
      >
        <Input
          id="password"
          type="password"
          autoComplete="new-password"
          aria-invalid={Boolean(errors.password)}
          {...register("password")}
        />
      </Field>
      <Field id="confirm" label="Confirm password" error={errors.confirm?.message}>
        <Input
          id="confirm"
          type="password"
          autoComplete="new-password"
          aria-invalid={Boolean(errors.confirm)}
          {...register("confirm")}
        />
      </Field>
    </>
  );
}
