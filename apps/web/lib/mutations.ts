// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import {
  type DefaultError,
  useMutation,
  type UseMutationOptions,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useRef } from "react";

import { ApiError } from "./api";

export interface MutationContext {
  /** Send as the Idempotency-Key header on a create. Stable across retries of one submit. */
  idempotencyKey: string;
}

type Options<TData, TVariables> = Omit<
  UseMutationOptions<TData, DefaultError, TVariables>,
  "mutationFn"
> & { mutationFn: (variables: TVariables, context: MutationContext) => Promise<TData> };

/** A rejected request (4xx) never created anything, so the next submit is a new attempt. */
function wasRejected(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

/**
 * A mutation that carries an Idempotency-Key. Retrying after a network error or a 5xx reuses the
 * key, so a create that actually landed isn't made twice; a success or a 4xx starts afresh.
 * Updates carry the record's `row_version` in their body instead; a stale one returns 409 and the
 * query client shows the reload prompt.
 */
export function useApiMutation<TData = unknown, TVariables = void>(
  options: Options<TData, TVariables>,
): UseMutationResult<TData, DefaultError, TVariables> {
  const key = useRef<string | undefined>(undefined);
  const { mutationFn, onSuccess, onError, ...rest } = options;
  return useMutation<TData, DefaultError, TVariables>({
    ...rest,
    mutationFn: (variables) => {
      key.current ??= crypto.randomUUID();
      return mutationFn(variables, { idempotencyKey: key.current });
    },
    onSuccess: (...args) => {
      key.current = undefined;
      return onSuccess?.(...args);
    },
    onError: (error, ...args) => {
      if (wasRejected(error)) key.current = undefined;
      return onError?.(error, ...args);
    },
  });
}
