# Six-step Harbourline demo

Run the setup in README first. All documents, people and numbers below are fictional.
Use one tab; click New chat at the start. Wait until attachment cards say Ready or
Partial before sending. Partial means some content needs review; a correct answer
must disclose uncertainty rather than invent a cell.

1. **Upload a rate-table image.** Attach `data/gate1_images/rates_00.png`.
   Type: **“From my attached image, what are the general and senior rates for 12 months?”**
   Correct: the uploaded fixture says **6.50% general, 7.00% senior**, with a source
   marked “your file”. This is an upload example, not the bank's current rate card.
   The sent image card remains openable; questionable OCR cells must be flagged.
2. **Ask about a PDF.** No attachment; the sample PDF is already in bank knowledge.
   Type: **“What are the age limits for a home loan applicant? Cite the document.”**
   Correct: **21 to 65**, with `Loan eligibility` / `loan_eligibility.pdf`, page and
   version in Sources. Alternatively attach that PDF and ask “According to my PDF,
   what are the age limits for a home loan applicant?”; the source must be your file.
3. **Look up an exact rate.** No attachment.
   Type: **“What is the fixed deposit rate for 12 months for a general customer?”**
   Correct: **7.10%**, effective **2026-10-01**, from the current rate card; never
   the superseded card or the different uploaded fixture from step 1.
4. **Log in and retrieve the balance.** No attachment.
   Type: **“Show my balance. Mobile 9000000001, account 000000000001.”**
   Correct: asks for a six-digit demo OTP, with no balance yet. Type **“123456”**.
   Correct: login chip shows verified, the held balance question replays, balance
   is **₹57,600.00**, account shown only as **0001** / masked. OTP is not in restored history.
5. **Complete a cheque book flow.** No attachment.
   Type **“I need a new chequebook”**; click **40 pages**, **Emergency (within 1 week)**,
   then **Address on file**. Correct: each click appears as a sent message; a summary
   shows pages, delivery and address, with Confirm / Change / Cancel. Click **Confirm**.
   Correct: exactly one request, a **CB-…** reference and expected date; repeated Confirm
   must not create a second request. No submission before confirmation.
6. **Ask a multi-part question.** No attachment; stay in the verified chat.
   Type: **“Show my balance, last 5 transactions and the current 12-month general FD rate.”**
   Correct: one reply contains **₹57,600.00**, exactly five newest-first transactions
   with code-formatted amounts, and **7.10%** from the current rate card. It must not
   invent a rate, expose a full account number, or ask for another OTP while still verified.

The release report lists known failing live cases. A safe inability-to-answer is a
visible limitation, not a successful substitute for the grounded answer described above.
