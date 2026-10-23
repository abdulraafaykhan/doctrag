import type { FileChip } from "./types";
const positive = (value: unknown, fallback: number) => {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : fallback;
};
export const MAX_UPLOAD_MB = positive(import.meta.env.VITE_MAX_UPLOAD_MB, 5);
export const MAX_MESSAGE_CHARS = positive(
  import.meta.env.VITE_MAX_MESSAGE_CHARS,
  2000,
);
export const MAX_CHAT_FILES = 5;
export const MAX_UPLOAD_FILES = 3;
export const ACCEPT = ".pdf,.docx,.pptx,.txt,.md,.csv,.tsv,.xlsx";
export function validateFiles(
  files: File[],
  existing: FileChip[],
  retryName?: string,
): string | null {
  if (files.length > MAX_UPLOAD_FILES)
    return "Choose at most 3 files at a time. Each chat can hold up to 5 files.";
  if (retryName && (files.length !== 1 || files[0].name !== retryName))
    return `Choose ${retryName} again to retry this file.`;
  const occupied = existing.filter(
    (file) => file.id || file.status === "uploading",
  ).length;
  if (!retryName && occupied + files.length > MAX_CHAT_FILES)
    return `This chat can hold up to 5 files. ${Math.max(0, MAX_CHAT_FILES - occupied)} file slots remain.`;
  for (const file of files) {
    if (!file.size) return `${file.name} is empty. Choose a file with content.`;
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024)
      return `${file.name} exceeds the ${MAX_UPLOAD_MB} MB per-file limit.`;
    const extension = "." + file.name.split(".").pop()?.toLowerCase();
    if (!ACCEPT.split(",").includes(extension))
      return `${file.name} is not a supported file type. Choose PDF, Office, text or CSV files.`;
  }
  return null;
}
