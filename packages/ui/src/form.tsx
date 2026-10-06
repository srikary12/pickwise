// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// Form primitives over react-hook-form + zod. A form built from these gets, without extra work:
//   - labels, hints and per-field errors wired for screen readers (aria-invalid, aria-describedby)
//   - an error summary that takes focus after a failed submit, each entry linking to its field
//   - values kept as strings for money and dates, so nothing passes through a float or a timezone
import { zodResolver } from "@hookform/resolvers/zod";
import { type ReactNode, useEffect, useRef } from "react";
import {
  type DefaultValues,
  type FieldErrors,
  type FieldPath,
  type FieldValues,
  type UseFormRegister,
  type UseFormReturn,
  useForm,
} from "react-hook-form";
import { z } from "zod";

import { Alert } from "./alert";
import { Input, Select } from "./input";
import { Field } from "./label";
import { cn } from "./lib/utils";

/** `useForm` with a zod schema as the validator. */
export function useZodForm<S extends z.ZodType<FieldValues, FieldValues>>(
  schema: S,
  defaultValues: DefaultValues<z.input<S>>,
): UseFormReturn<z.input<S>, unknown, z.output<S>> {
  return useForm<z.input<S>, unknown, z.output<S>>({
    // The resolver's generics are looser than useForm's; the schema is the source of truth.
    resolver: zodResolver(schema as never) as never,
    defaultValues,
    mode: "onTouched",
  });
}

/** Rupees as a decimal string: up to 12 digits, up to 2 decimals ("1500", "1500.5", "0.25"). */
export const moneySchema = (message = "Enter an amount like 1500 or 1500.50.") =>
  z
    .string()
    .trim()
    .regex(/^\d{1,12}(\.\d{1,2})?$/, message);

/** A calendar date as "YYYY-MM-DD" (what <input type="date"> produces). */
export const dateSchema = (message = "Enter a valid date.") =>
  z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, message)
    .refine((value) => !Number.isNaN(Date.parse(`${value}T00:00:00Z`)), message);

function fieldId(name: string): string {
  return `field-${name.replaceAll(".", "-")}`;
}

/** What the field components need from a form: any `useForm`/`useZodForm` result fits. */
interface FormLike<T extends FieldValues> {
  register: UseFormRegister<T>;
  formState: { errors: FieldErrors<T> };
}

function messageOf(form: { formState: { errors: unknown } }, name: string): string | undefined {
  const parts = name.split(".");
  let node: unknown = form.formState.errors;
  for (const part of parts) {
    node = (node as Record<string, unknown> | undefined)?.[part];
  }
  const message = (node as { message?: unknown } | undefined)?.message;
  return typeof message === "string" ? message : undefined;
}

interface FormProps<T extends FieldValues, O extends FieldValues> {
  form: UseFormReturn<T, unknown, O>;
  onSubmit: (values: O) => void | Promise<void>;
  /** Names the form for screen readers when the page has more than one. */
  label?: string;
  children: ReactNode;
  className?: string;
}

/** <form> with an error summary: after a failed submit, focus moves to the list of problems. */
export function Form<T extends FieldValues, O extends FieldValues>({
  form,
  onSubmit,
  label,
  children,
  className,
}: FormProps<T, O>) {
  const summary = useRef<HTMLDivElement>(null);
  const { errors, submitCount, isValid } = form.formState;
  const problems = flatten(errors);

  // Only after a submit attempt that left errors; not on every keystroke.
  useEffect(() => {
    if (submitCount > 0 && !isValid && problems.length > 0) summary.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per submit attempt
  }, [submitCount]);

  return (
    <form
      noValidate
      aria-label={label}
      className={cn("flex flex-col gap-4", className)}
      onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
    >
      {submitCount > 0 && problems.length > 0 ? (
        <div ref={summary} tabIndex={-1} className="outline-none">
          <Alert variant="destructive">
            <p className="font-medium">
              {problems.length === 1
                ? "Fix this to continue:"
                : `Fix these ${problems.length} to continue:`}
            </p>
            <ul className="mt-1 list-disc pl-5">
              {problems.map((problem) => (
                <li key={problem.name}>
                  <a href={`#${fieldId(problem.name)}`} className="underline underline-offset-2">
                    {problem.message}
                  </a>
                </li>
              ))}
            </ul>
          </Alert>
        </div>
      ) : null}
      {children}
    </form>
  );
}

function flatten(errors: unknown, prefix = ""): { name: string; message: string }[] {
  if (!errors || typeof errors !== "object") return [];
  const out: { name: string; message: string }[] = [];
  for (const [key, value] of Object.entries(errors)) {
    const name = prefix ? `${prefix}.${key}` : key;
    const message = (value as { message?: unknown } | null)?.message;
    if (typeof message === "string") out.push({ name, message });
    else out.push(...flatten(value, name));
  }
  return out;
}

interface BaseProps<T extends FieldValues> {
  form: FormLike<T>;
  name: FieldPath<T>;
  label: string;
  hint?: string;
  className?: string;
}

function describedBy(id: string, error: string | undefined, hint: string | undefined) {
  return error ? `${id}-error` : hint ? `${id}-hint` : undefined;
}

export function TextField<T extends FieldValues>({
  form,
  name,
  label,
  hint,
  className,
  type = "text",
  autoComplete,
  placeholder,
}: BaseProps<T> & {
  type?: "text" | "email" | "tel" | "url" | "password";
  autoComplete?: string;
  placeholder?: string;
}) {
  const id = fieldId(name);
  const error = messageOf(form, name);
  return (
    <Field id={id} label={label} error={error} hint={hint} className={className}>
      <Input
        id={id}
        type={type}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        autoComplete={autoComplete}
        placeholder={placeholder}
        {...form.register(name)}
      />
    </Field>
  );
}

export function SelectField<T extends FieldValues>({
  form,
  name,
  label,
  hint,
  className,
  options,
  placeholder,
}: BaseProps<T> & {
  options: readonly { value: string; label: string }[];
  /** An empty first option, e.g. "Choose…". */
  placeholder?: string;
}) {
  const id = fieldId(name);
  const error = messageOf(form, name);
  return (
    <Field id={id} label={label} error={error} hint={hint} className={className}>
      <Select
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        {...form.register(name)}
      >
        {placeholder ? <option value="">{placeholder}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
    </Field>
  );
}

/** A calendar date; the value is the "YYYY-MM-DD" string (pair with `dateSchema`). */
export function DateField<T extends FieldValues>({
  form,
  name,
  label,
  hint,
  className,
  min,
  max,
}: BaseProps<T> & { min?: string; max?: string }) {
  const id = fieldId(name);
  const error = messageOf(form, name);
  return (
    <Field id={id} label={label} error={error} hint={hint} className={className}>
      <Input
        id={id}
        type="date"
        min={min}
        max={max}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        {...form.register(name)}
      />
    </Field>
  );
}

/** An amount in rupees; the value is a decimal string (pair with `moneySchema`). */
export function MoneyField<T extends FieldValues>({
  form,
  name,
  label,
  hint,
  className,
}: BaseProps<T>) {
  const id = fieldId(name);
  const error = messageOf(form, name);
  return (
    <Field id={id} label={label} error={error} hint={hint} className={className}>
      <div className="relative">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground"
        >
          ₹
        </span>
        <Input
          id={id}
          inputMode="decimal"
          autoComplete="off"
          className="pl-7 text-right tabular-nums"
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, error, hint)}
          {...form.register(name)}
        />
      </div>
    </Field>
  );
}
