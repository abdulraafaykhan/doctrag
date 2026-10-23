# Documents, service catalog and data

The shared knowledge base is rebuilt: the old documents (which say "Lantern Demo Bank") are replaced by about 20 Harbourline documents, and retrieval is made to work properly.

## 1. Document rules

- Files live in `seed_docs/` as Markdown. Each starts with a small header:
  ```
  Title: <title>
  Type: Real public rule | Fictional Harbourline policy
  Source: <official URL>        (real rules only)
  As of: <month year>           (real rules only)
  Note: Summary in plain words. Not legal advice.   (real rules only)
  ```
- **Real public rules** (RBI, NPCI, DICGC): write them in your own words, do not copy text, name the source and the date, and verify each fact on the official page when you write it. If you cannot verify a fact, leave it out. If you have no web access at all, **do not write the real-rule documents from memory**: write only the fictional ones and tell me which real-rule documents are still to do and which facts to check.
- **Fictional policies** state clearly that they are Harbourline Bank's (fictional) policy.
- Written in plain, customer-friendly English. Use headings, short paragraphs, and tables where numbers matter (fees, rates, limits). Tables keep their header row repeated when chunked (the existing table chunking does this).
- Each document is about 300 to 900 words, one topic each, with question-like headings ("How long does a cheque book take?") so retrieval finds them.
- Harbourline Bank, Mumbai, INR, India. Branch names, addresses and phone numbers are **fake**. Helpline numbers use an obviously fake pattern, never a plausible real number.
- Never put a real person, a real bank's name as ours, or secrets in a document.

## 2. The documents (about 20)

**Real public rules (verify before writing):**
1. Customer liability for unauthorised electronic banking transactions (RBI), reporting timelines and liability limits.
2. KYC for bank accounts (RBI): what is required, periodic updation, what happens with incomplete KYC.
3. Complaints and the Reserve Bank Integrated Ombudsman Scheme: when and how to escalate.
4. Deposit insurance (DICGC): what is covered and the limit per depositor per bank.
5. Cheques: validity period, clearing, common reasons for return.
6. NEFT, RTGS and IMPS: availability, minimum or maximum amounts, charges rules.
7. UPI: limits, safety, what to do about a wrong transfer (NPCI).
8. Failed transactions and compensation for delays (RBI turnaround time rules).
9. Loans: fair practice, prepayment rules for individual borrowers on floating-rate loans, key fact statement.
10. Card transaction controls and safety: enabling or disabling online, international and contactless use (RBI).

**Fictional Harbourline policies (must agree with the catalog in section 3):**
11. Savings and current accounts: types, minimum balance, features.
12. Fees and charges schedule (cheque book, debit card, SMS, transfers, SMS alerts).
13. Cheque book policy: leaves, eligibility, normal and urgent delivery, how to request.
14. Debit card terms: networks, limits, blocking, replacement, delivery.
15. Credit card terms: limit, billing cycle, interest, late fee, reporting a lost card.
16. Loan products: home, personal, car, education, with rates, amounts, tenures (the EMI catalog) and how to apply.
17. Fixed and recurring deposits: rates, tenures, premature closure.
18. Branches and contact: fake Mumbai branches, timings, helpline and email (fake), how to change address or mobile number (at a branch).
19. Complaints at Harbourline: how to raise one, timelines, escalation to the ombudsman.
20. Digital banking safety and request turnaround times: phishing, OTP safety, what the bank never asks, status of requests.

## 3. Service catalog (single source of truth)

`seed_data/bank/service_catalog.json` holds these values. The flows read it, and documents 12, 13, 14 and 16 must quote exactly the same numbers. Amounts are in rupees here (store minor units in the file).

| Item | Value |
| --- | --- |
| Cheque book 25 leaves | fee 0 |
| Cheque book 50 leaves | fee 50 |
| Cheque book 75 leaves | fee 100 |
| Cheque book normal delivery | 5 to 7 working days, free |
| Cheque book urgent delivery | 2 to 3 working days, extra fee 150 |
| New or replacement debit card | fee 200, delivery 5 to 7 working days |
| Debit card networks | RuPay, Visa |
| Card block | instant, free |
| Mini-statement | last 10 transactions, free |
| Home loan | 8.75% a year, 5,00,000 to 5,00,00,000, 60 to 360 months |
| Personal loan | 11.50% a year, 50,000 to 25,00,000, 12 to 72 months |
| Car loan | 9.25% a year, 1,00,000 to 1,50,00,000, 12 to 84 months |
| Education loan | 9.50% a year, 50,000 to 1,00,00,000, 12 to 180 months |

All loan rates and fees are fictional and indicative.

## 4. Seeding and retrieval

- `scripts/seed_kb.py` must make the shared knowledge base **exactly equal to `seed_docs/`**: add new files, re-embed changed ones, and delete shared documents (and their chunks) that are no longer in `seed_docs/`. Rerunning changes nothing if nothing changed.
- Remove the old five documents. No "Lantern" must remain anywhere (search for it).
- Retrieval quality is checked, not assumed. Add about 40 document questions to `eval/cases.jsonl` with an `expect_doc` field (the title that should be in the top results) and report two numbers: **retrieval hit rate** (the expected document is in the top 3) and **answer groundedness** (an answer with a citation, or the fixed "couldn't find"). Aim for a hit rate of 85% or better. If a topic fails, fix the document headings or chunking, not the test.
- Multi-part questions must be answered in full (see `queries` in `REDESIGN.md` section 4).
- The fixed "couldn't find" reply must still appear when the documents do not contain the answer.

## 5. Customer data additions

The ten fixture customers stay. Add what the flows need, in the existing JSON files:
- `cards.json`: at least one customer with two cards (debit and credit) so the card choice step is exercised; CUST-005 keeps a blocked card.
- `accounts.json`: CUST-006's account has `cheque_book_eligible: false`; CUST-007's customer has `kyc_status: incomplete`.
- `service_requests.json`: CUST-008 has an open cheque book request with a reference in the `HB-CHQ-2026-######` format. A few completed historical requests for other customers.
- `scripts/generate_bank_data.py` stays deterministic. `scripts/seed_bank.py --reset` restores the fixtures (section 10 of `REDESIGN.md`).