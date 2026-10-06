// SPDX-License-Identifier: AGPL-3.0-only
// Browser upload through the files pipeline: slot → object store → complete → wait for the scan.
import { api, ApiError, call } from "./api";

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** Browsers report odd types for spreadsheets (Windows says CSV is "application/vnd.ms-excel"). */
function mimeFor(file: File): string {
  const name = file.name.toLowerCase();
  if (name.endsWith(".csv")) return "text/csv";
  if (name.endsWith(".xlsx")) return XLSX;
  return file.type || "application/octet-stream";
}

export interface UploadOptions {
  classification?: "public" | "internal" | "confidential" | "restricted";
  /** Attach the file to a record so the owning module can decide who may read it. */
  owner?: { type: string; id: string };
  /** Reports "uploading" then "scanning". */
  onStage?: (stage: "uploading" | "scanning") => void;
  signal?: AbortSignal;
}

/** Uploads `file` and resolves with the file id once it has passed the virus scan. */
export async function uploadAndScan(file: File, options: UploadOptions = {}): Promise<string> {
  const slot = await call(
    api().POST("/v1/files", {
      body: {
        filename: file.name,
        mime_type: mimeFor(file),
        size_bytes: file.size,
        classification: options.classification ?? "confidential",
        ...(options.owner
          ? { owner_entity_type: options.owner.type, owner_entity_id: options.owner.id }
          : {}),
      },
    }),
  );
  options.onStage?.("uploading");
  const form = new FormData();
  for (const [key, value] of Object.entries(slot.upload_fields)) form.append(key, value);
  form.append("file", file);
  const stored = await fetch(slot.upload_url, {
    method: "POST",
    body: form,
    ...(options.signal ? { signal: options.signal } : {}),
  });
  if (!stored.ok)
    throw new ApiError(stored.status, "upload_failed", "The upload didn't go through.");
  const fileId = slot.file.id;
  await call(api().POST("/v1/files/{file_id}/complete", { params: { path: { file_id: fileId } } }));
  options.onStage?.("scanning");
  for (let attempt = 0; attempt < 60; attempt++) {
    options.signal?.throwIfAborted();
    const status = await call(
      api().GET("/v1/files/{file_id}", { params: { path: { file_id: fileId } } }),
    );
    if (status.scan_status === "clean") return fileId;
    if (status.scan_status !== "pending") {
      throw new ApiError(422, "file_rejected", status.scan_detail ?? "The file was rejected.");
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new ApiError(0, "scan_timeout", "The virus scan is taking long. Try again shortly.");
}
