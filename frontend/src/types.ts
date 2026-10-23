export interface ChatSession {
  session_id: string;
}
export interface Source {
  n: number;
  filename: string;
  location?: string;
  page?: number | null;
  text?: string;
  snippet?: string;
  doc_id: string;
  chunk_index: number;
  cited?: boolean;
}
export interface Balance {
  type: string;
  nickname: string;
  masked_number: string;
  currency: string;
  balance: string;
}
export interface Transaction {
  type: string;
  nickname: string;
  masked_number: string;
  posted_at: string;
  description: string;
  direction: string;
  currency: string;
  amount: string;
}
export interface BankCard {
  kind: string;
  last4: string;
  status: string;
  expiry: string;
}
export type AccountCard =
  | { type: "balances"; accounts: Balance[] }
  | { type: "transactions"; transactions: Transaction[] }
  | { type: "cards"; cards: BankCard[] };
export interface FileRef {
  id: string;
  filename: string;
}
export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
  sources: Source[];
  attachments: FileRef[];
  account_cards?: AccountCard[];
}
export type FileStatus =
  "uploading" | "processing" | "ready" | "failed" | "rejected";
export interface FileChip {
  key: string;
  id?: string;
  filename: string;
  status: FileStatus;
  error?: string;
}
export interface Attachment {
  id: string;
  filename: string;
  status: "processing" | "ready" | "failed";
  size_bytes: number;
  warnings: string[];
}
export interface UploadResult {
  filename: string;
  status: "processing" | "ready" | "failed" | "rejected";
  doc_id?: string;
  duplicate?: boolean;
  error?: string;
}
export interface Message {
  key: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  cards: AccountCard[];
  attachments: FileRef[];
  notes: string[];
  pending?: boolean;
  error?: string;
}
export type ChatEvent =
  | { type: "status"; text: string }
  | {
      type: "meta";
      session_id: string;
      sources: Source[];
      account_cards?: AccountCard[];
      clear_history?: boolean;
      notes?: string[];
    }
  | { type: "token"; t: string }
  | { type: "done"; session_id: string; cited: number[] }
  | { type: "error"; detail: string };
