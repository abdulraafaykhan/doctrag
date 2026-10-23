import { describe, expect, it } from "vitest";
import { validateFiles, MAX_UPLOAD_MB } from "./limits";
import type { FileChip } from "./types";
const file = (name = "notes.txt") => new File(["fictional"], name);
describe("client upload limits", () => {
  it("enforces three per upload, five per chat and supported formats", () => {
    expect(validateFiles([file(), file(), file(), file()], [])).toMatch(/3/);
    const chips: FileChip[] = Array.from({ length: 5 }, (_, i) => ({
      key: String(i),
      id: String(i),
      filename: "notes.txt",
      status: "ready",
    }));
    expect(validateFiles([file()], chips)).toMatch(/5/);
    expect(validateFiles([file("program.exe")], [])).toMatch(
      /supported|support/i,
    );
    expect(validateFiles([file("notes.md")], [])).toBeNull();
  });
  it("rejects empty or oversized files with clear messages", () => {
    expect(validateFiles([new File([], "empty.txt")], [])).toMatch(/empty/i);
    const large = new File(
      [new Uint8Array(MAX_UPLOAD_MB * 1024 * 1024 + 1)],
      "large.txt",
    );
    expect(validateFiles([large], [])).toContain(`${MAX_UPLOAD_MB} MB`);
  });
  it("allows an explicit failed-file retry at the chat limit", () => {
    const chips: FileChip[] = Array.from({ length: 5 }, (_, i) => ({
      key: String(i),
      id: String(i),
      filename: "notes.txt",
      status: "failed",
    }));
    expect(validateFiles([file()], chips, "notes.txt")).toBeNull();
    expect(validateFiles([file("other.txt")], chips, "notes.txt")).toMatch(
      /same|notes.txt/i,
    );
  });
});
