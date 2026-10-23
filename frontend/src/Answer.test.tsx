import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Answer, SourceDialog } from "./Answer";
import { AccountCards } from "./AccountCards";
const source = {
  n: 1,
  filename: "fees.md",
  doc_id: "d",
  chunk_index: 0,
  snippet: "Replacement fee: INR 150.",
};
it("renders GFM tables and citation buttons without executing HTML or loading remote images", async () => {
  const click = vi.fn();
  const { container } = render(
    <Answer
      text={
        'Fee [1]\n\n| Item | Fee |\n| --- | --- |\n| Card | INR 150 |\n\n<script>alert(1)</script>\n\n<img src="https://example.com/tracker">\n\n![tracking](https://example.com/tracker)\n\n[unsafe](javascript:alert(1))'
      }
      sources={[source]}
      onSource={click}
    />,
  );
  expect(screen.getByRole("table")).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Open source 1: fees.md" }),
  );
  expect(click).toHaveBeenCalledWith(source);
  expect(container.querySelector("script,img,iframe")).toBeNull();
  expect(container.querySelector('[href^="javascript:"]')).toBeNull();
});
it("shows the actual excerpt and explains missing historical passage text", () => {
  const { rerender } = render(
    <SourceDialog source={source} onClose={vi.fn()} />,
  );
  expect(screen.getByText("Replacement fee: INR 150.")).toBeVisible();
  rerender(
    <SourceDialog
      source={{ ...source, snippet: undefined }}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByText(/Passage text was not saved/)).toBeVisible();
});
it("keeps account display amounts exactly as formatted by the server for all card types", () => {
  render(
    <AccountCards
      cards={[
        {
          type: "balances",
          accounts: [
            {
              type: "savings",
              nickname: "Everyday",
              masked_number: "••1234",
              balance: "INR 6,289.80",
              currency: "INR",
            },
          ],
        },
        {
          type: "transactions",
          transactions: [
            {
              type: "savings",
              nickname: "Everyday",
              masked_number: "••1234",
              posted_at: "2026-10-01T12:00:00Z",
              description: "Fictional shop",
              direction: "debit",
              amount: "INR 140.05",
              currency: "INR",
            },
          ],
        },
        {
          type: "cards",
          cards: [
            {
              kind: "debit",
              last4: "5678",
              status: "active",
              expiry: "12/2029",
            },
          ],
        },
      ]}
    />,
  );
  expect(
    screen.getAllByRole("region", { name: "From your account" }),
  ).toHaveLength(3);
  expect(screen.getByText("INR 6,289.80")).toBeVisible();
  expect(screen.getByText("INR 140.05")).toBeVisible();
  expect(screen.getByText("active")).toBeVisible();
});
