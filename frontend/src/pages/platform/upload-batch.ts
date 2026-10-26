export interface UploadEntry {
  id: string;
  file: File;
  status: "pending" | "uploading" | "submitted" | "failed" | "rejected";
  error?: string;
  jobId?: string;
}

export function uploadIssue(file: Pick<File, "size">, limitBytes: number): string | undefined {
  if (!file.size) return "文件为空";
  if (file.size > limitBytes) return `超过单文件上限 ${(limitBytes / 1024 / 1024).toFixed(0)} MB`;
}

// Each accepted file owns a durable job. A failed sibling must neither hide that
// job nor cause it to be submitted again when retrying this batch.
export async function submitUploadBatch(
  entries: UploadEntry[],
  limitBytes: number,
  upload: (file: File) => Promise<string>,
  update: (entry: UploadEntry) => void,
  errorText: (error: unknown) => string,
): Promise<UploadEntry[]> {
  const result: UploadEntry[] = [];
  for (const entry of entries) {
    if (entry.status === "submitted") { result.push(entry); continue; }
    const issue = uploadIssue(entry.file, limitBytes);
    let next: UploadEntry;
    if (issue) next = { ...entry, status: "rejected", error: issue };
    else {
      update({ ...entry, status: "uploading", error: undefined });
      try { next = { ...entry, status: "submitted", error: undefined, jobId: await upload(entry.file) }; }
      catch (error) { next = { ...entry, status: "failed", error: errorText(error) }; }
    }
    result.push(next);
    update(next);
  }
  return result;
}
