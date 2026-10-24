# Walkthrough: four documents through the engine

Everything below is real output from `testamur explain` on the seed-11 tiny world, with its scans and photos. None of
these four documents needs the model, so the output is the same with or without one. Document and claim numbers are
stable for a given seed. To reproduce:

```bash
testamur world --seed 11 --size tiny --out data/world_tiny
testamur harness --world data/world_tiny --db data/harness_tiny.db --report data/harness_tiny.md
testamur explain 1 authority --db data/harness_tiny.db --tenant 1
testamur explain 4 bank_details --db data/harness_tiny.db --tenant 1
testamur explain 15 proof_of_claim --db data/harness_tiny.db --tenant 2
testamur explain 10 authority --db data/harness_tiny.db --tenant 2
```

## 1. A phone photo of a debenture trustee's authority

**What arrived.** `SCN61429.jpg`, uploaded by the firm's staff into the process of Konkan Tidewater Marine Pvt Ltd.
A phone photo: skewed, unevenly lit, slightly blurred. Nothing in the filename says what it is or whose.

**Extraction.** There is no text layer (it is an image), so the page goes to OCR: one page, the only paid step for
this document. RapidOCR returns text boxes; boxes whose vertical spans overlap are joined into lines, left to right.
The result is cached under the file's hash and the extractor version.

**Classification.** The first line is "CERTIFICATE OF AUTHORISATION", one of the letter of authority's title phrases
(0.6), and the page carries all three of the type's key labels: Financial creditor, Authorised representative and
Valid until (0.4). Score 1.0 for a letter of authority, well above the acceptance threshold (0.55) and the required
margin (0.2): decided by the rules, no model.

**Matching.** A letter of authority carries no identifier with check characters, so the name decides. "Pratibhuti
Trusteeship Services Ltd" is compared with the creditors who have a claim in this process and the trustees acting for
them: the trustee scores 100, the next name is 68 points behind. Matched by name. The page lists the debenture
holders the trustee acts for ("Debenture holders represented"), and each name is resolved among that trustee's own
holders only: one of them, Shaurya Credit Private Limited, holds claim 1.

**Checks and status.** Claim 1 has no authority of its own on file, so the trustee's decides, through cover:

```
authority for claim 1: COVERED_BY_TRUSTEE (document 47)
  [field   ] auth_creditor_matches        PASS    'Pratibhuti Trusteeship Services Ltd' matches 'Pratibhuti Trusteeship Services Ltd' (100)
             evidence p.1: Financial creditor: Pratibhuti Trusteeship Services Ltd
  [field   ] auth_in_effect               PASS    in effect since 2025-12-05
             evidence p.1: Effective from: 05/12/2025
  [field   ] auth_current                 PASS    2026-12-05 is on or after 2026-10-01
             evidence p.1: Valid until: 05.12.2026
  [field   ] auth_renewal_due             PASS    2026-12-05 is on or after 2026-10-31
             evidence p.1: Valid until: 05.12.2026
  [field   ] auth_issued_recently         PASS    dated 2025-11-27, 308 days ago
             evidence p.1: Date of issue: 27-Nov-2025
  [field   ] auth_covers_voting           PASS    grants it: 'To represent the creditor and vote in the committee of creditors'
             evidence p.1: Powers: To represent the creditor and vote in the committee of creditors
```

The name check compares the page with the trustee, the party that issued it, not with the holder: a trustee's
authority in the trustee's own name is exactly what section 21(6A)(a) expects. COVERED_BY_TRUSTEE vouches, but only
while nothing else could decide: had a document that might be the holder's own authority been waiting for a person,
the status would be NEEDS_REVIEW, because the holder's own document would take precedence.

## 2. A scanned bank letter that changes the account

**What arrived.** `Document (1).pdf`, a scan, through the staff's verified channel (a call-back to a known number),
into the process of Pench Valley Polymers Pvt Ltd.

**Reading and matching.** OCR, one page. The page names Sindhu Co-operative Bank and an IFSC beginning SNDX, which is
that bank's code; the account number and the IFSC are well formed. As an identifier, though, the account (the IFSC
with its account number) belongs to no creditor on file, so it decides nothing, and the name does: "Aranya Motor
Finance Ltd" scores 100 inside the process, 18 points clear of the next.

```
bank_details for claim 4: NOT_SATISFIED (document 49)
  [metadata] bank_channel_verified        PASS    received through a verified channel
             evidence record: channels: staff_verified
  [field   ] bank_ifsc_valid              PASS    IFSC of Sindhu Co-operative Bank (SNDX)
             evidence p.1: IFSC: SNDX0968426
             evidence p.1: Bank: Sindhu Co-operative Bank
  [field   ] bank_account_on_file         FAIL    differs from the record on file
             evidence p.1: Account number: 61045490824869
             fix: These bank details differ from the account on file. Changed bank details are the classic payment-fraud pattern: verify the change by call-back before updating.
  [field   ] bank_holder_matches          PASS    'Aranya Motor Finance Ltd' matches 'Aranya Motor Finance Ltd' (100)
             evidence p.1: Account holder: Aranya Motor Finance Ltd
  [field   ] bank_letter_recent           PASS    dated 2026-08-05, 57 days ago
             evidence p.1: Date: 05-08-2026
```

Everything on the page is consistent, and that is the point: a letter moving a creditor's payouts to another bank is
well formed by design. Only the record on file shows the change. The account check is critical, so the claim's
bank details are NOT_SATISFIED and the claim goes on hold (no payout) until a person verifies the change. "record"
evidence comes from the claim and upload records rather than the page.

## 3. A proof of claim above the corporate debtor's books

**What arrived.** `aranya_form c.pdf`, born digital, uploaded through the creditor portal into the process of
Vidarbha Cold Chain Ltd.

**Reading.** A text layer, so no OCR at all. The title "FORM C" and the line "PROOF OF CLAIM BY FINANCIAL CREDITORS"
match the type, all three key labels are there, and the filename helps a little: 1.1. The CIN, GSTIN and LEI on the
form validate, and one creditor of this firm holds them: matched by identifier, the strongest kind of match. The
form names this process's corporate debtor, so it is evidence in this process and no other.

```
proof_of_claim for claim 15: SATISFIED_WITH_FINDINGS (document 1)
  [metadata] received_by_due_date         PASS    received by the last date for claims
             evidence record: first received 2026-03-06
  [field   ] claim_against_debtor         PASS    'Vidarbha Cold Chain Ltd' matches 'Vidarbha Cold Chain Ltd' (100)
             evidence p.1: In the matter of: Vidarbha Cold Chain Ltd
  [field   ] claim_name_matches           PASS    'Aranya Motor Finance Services Ltd' matches 'Aranya Motor Finance Services Ltd' (100)
             evidence p.1: Name of the financial creditor: Aranya Motor Finance Services Ltd
  [field   ] claim_within_books           FAIL    Rs. 4,39,56,000 exceeds the books (Rs. 3,41,30,000)
             evidence p.1: Total amount of claim: INR 4,39,56,000
             fix: The claim is Rs. 4,39,56,000; the corporate debtor's books show Rs. 3,41,30,000. Ask Aranya Motor Finance Services Ltd for a reconciliation and the documents behind the difference (regulation 13(2)).
  [field   ] claim_words_match            PASS    the amount in words agrees with the figures
             evidence p.1: Total amount of claim: INR 4,39,56,000
             evidence p.1: Amount in words: Rupees Four Crore Thirty-Nine Lakh Fifty-Six Thousand Only
  [field   ] claim_not_time_barred        PASS    2024-01-28 is on or after 2023-02-23
             evidence p.1: Date of default: 28/01/2024
  [field   ] claim_dated_after_announcement PASS    2026-03-02 is on or after 2026-02-24
             evidence p.1: Date of claim: 02.03.2026
  [field   ] claim_gstin_valid            PASS    the GSTIN's check character is valid
             evidence p.1: GSTIN: 33WHPCA8546K1Z6
  [field   ] claim_pan_in_gstin           PASS    the PAN is the one inside the GSTIN
             evidence p.1: PAN: WHPCA8546K
             evidence p.1: GSTIN: 33WHPCA8546K1Z6
  [field   ] claim_lei_valid              PASS    LEI check digits are valid
             evidence p.1: LEI: VY4200QM0O5TY1XN0793
```

"INR 4,39,56,000" is read in lakh and crore grouping, and the words are read the Indian way, scales in order; the two
agree. The default on 28 January 2024 is within three years of the insolvency commencement date (23 February 2026), so
the claim is not time-barred. It is, however, about 29% above what the corporate debtor's books show, beyond the 2%
the pack allows for interest accrued since the books closed. That check is major, not critical: the claim stands, the
finding is recorded with the line it came from, and the hint asks for the reconciliation the regulations let the
resolution professional ask for.

## 4. An authority that contradicts itself

**What arrived.** `IMG-20260624-WA1171.pdf`, a two-page scan through the creditor portal, into the process of
Satpura Foundry Works Ltd. Page two is headed "AMENDMENT TO THE LETTER OF AUTHORITY".

```
authority for claim 10: NEEDS_REVIEW (document 30)
  [field   ] auth_creditor_matches        PASS    'Bhavya Motor Finance Pvt Ltd' matches 'Bhavya Motor Finance Pvt Ltd' (100)
             evidence p.1: Financial creditor: Bhavya Motor Finance Pvt Ltd
  [field   ] auth_in_effect               PASS    in effect since 2026-03-23
             evidence p.1: Effective from: 23-03-2026
  [field   ] auth_current                 ABSTAIN the document states two different values: '23.03.2027' (page 1); '10-Jan-2027' (page 2)
  [field   ] auth_renewal_due             ABSTAIN the document states two different values: '23.03.2027' (page 1); '10-Jan-2027' (page 2)
  [field   ] auth_issued_recently         PASS    dated 2026-03-21, 194 days ago
             evidence p.1: Date of issue: 21-Mar-2026
  [field   ] auth_covers_voting           PASS    grants it: 'To attend and vote at meetings of the committee of creditors'
             evidence p.1: Powers: To attend and vote at meetings of the committee of creditors
```

Both dates are in the future, so either reading would PASS today, and an engine that picked one would look right.
It does not pick: the document contradicts itself about the field a critical check needs, so the check abstains, and
the requirement waits for a person (NEEDS_REVIEW). The person sees both readings with their pages, decides, and the
decision is kept as an input that the engine applies on every later run, bound to the rule it was made under.
