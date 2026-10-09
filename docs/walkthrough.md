# Walkthrough: three documents through the engine

Everything below is real output from `testamur explain` on the seed-7 small world. None of these three documents
needs the model, so the output is the same with or without one. Document and engagement numbers are stable for a
given seed. To reproduce:

```bash
testamur world --seed 7 --size small --out data/world_small
testamur harness --world data/world_small --db data/harness_small.db --report data/harness_small.md
testamur explain 3 insurance --db data/harness_small.db
testamur explain 13 bank_details --db data/harness_small.db
testamur explain 30 insurance --db data/harness_small.db --tenant 2
```

## 1. A phone photo of an insurance certificate

**What arrived.** `Document (2).jpg`, by email, into one business unit. A phone photo: skewed, unevenly lit, slightly
blurred. Nothing in the filename says what it is or whose.

**Extraction.** There is no text layer (it is an image), so the page goes to OCR: one page, the only paid step for
this document. RapidOCR returns text boxes; boxes whose vertical spans overlap are joined into lines, left to right.
The result is cached under the file's hash and the extractor version.

**Classification.** Page one's first line is "EVIDENCE OF INSURANCE", one of the insurance certificate's title
phrases (0.6). The page carries four of the type's five key labels: Insurer, Policy number, General liability limit
and Expiry date (0.4 x 4/5). The fifth, "Insured", appears here as "Policyholder", which the field reader accepts as
an alias but the classifier does not count. Score 0.92 for insurance certificate and 0 for every other type, well
above the acceptance threshold (0.55) and the required margin (0.2): decided by the rules, no model.

**Fields.** Each field is read after its label at the start of a line: `Policyholder: Ashford Applications Ltd`,
`General liability limit: 1.000.000 EUR any one occurrence` (parsed as EUR 1,000,000: one grouping separator used
consistently, groups of three), `Expiry date: 20/04/2027` (day first), `Date of issue: 20 April 2026`.

**Matching.** No identifier with check digits on an insurance certificate, so the name decides. "Ashford Applications
Ltd" is compared with the suppliers engaged in the business unit it was uploaded to: one scores 100, the next is 59
points behind. Matched by name.

**Checks and status.** The supplier's engagement is standard tier, which requires EUR 2,000,000 of cover.

```
insurance for engagement 3: NOT_SATISFIED (document 7)
  [metadata] received_before_start        PASS    received before the engagement started
             evidence record: first received 2025-08-03
  [field   ] ins_name_matches             PASS    'Ashford Applications Ltd' matches 'Ashford Applications Ltd' (100)
             evidence p.1: Policyholder: Ashford Applications Ltd
  [field   ] ins_limit_meets_tier         FAIL    limit EUR 1,000,000 is below EUR 2,000,000
             evidence p.1: General liability limit: 1.000.000 EUR any one occurrence
             fix: Cover is EUR 1,000,000; a standard supplier needs at least EUR 2,000,000. Ask Ashford Applications Ltd to raise the limit.
  [field   ] ins_cover_current            PASS    2027-04-20 is on or after 2026-10-01
             evidence p.1: Expiry date: 20/04/2027
  [field   ] ins_renewal_due              PASS    2027-04-20 is on or after 2026-10-31
             evidence p.1: Expiry date: 20/04/2027
  [field   ] ins_issued_recently          PASS    dated 2026-04-20, 164 days ago
             evidence p.1: Date of issue: 20 April 2026
```

A critical check failed, so the status is NOT_SATISFIED, with the line of the page that shows it and the action to
take. "record" evidence comes from the engagement and upload records rather than the page. Had the same certificate
been judged for a high-tier engagement of the same supplier, the limit check would compare against that tier's
minimum: one document, one verdict per engagement.

## 2. A bank letter whose account changed

**What arrived.** `bank_final.pdf`, uploaded by staff after a call-back verification. It is born digital, so its text
is read from the PDF and no OCR is paid for.

**Matching.** The letter's IBAN fails its check digits, so it is not used as an identifier; the account holder's
name, "Vantage Logistics Ltd", matches one supplier of the business unit exactly.

```
bank_details for engagement 13: NOT_SATISFIED (document 44)
  [metadata] bank_channel_verified        PASS    received through a verified channel
             evidence record: channels: staff_verified
  [field   ] bank_iban_valid              FAIL    IBAN check digits are invalid
             evidence p.1: IBAN: GB31 HTBK 2686 5633 5150 58
             fix: The IBAN fails its check digits; confirm the account details with Vantage Logistics Ltd by call-back.
  [field   ] bank_iban_on_file            FAIL    differs from the record on file
             evidence p.1: IBAN: GB31 HTBK 2686 5633 5150 58
             fix: These bank details differ from the account on file. Changed bank details are the classic payment-fraud pattern: verify the change by call-back before updating.
  [field   ] bank_holder_matches          PASS    'Vantage Logistics Ltd' matches 'Vantage Logistics Ltd' (100)
             evidence p.1: Account holder: Vantage Logistics Ltd
  [field   ] bank_letter_recent           PASS    dated 2026-06-01, 122 days ago
             evidence p.1: Date: 2026-06-01
```

Two critical failures, each with its own fix. Neither needed a model: check digits and a comparison with the record
on file are free and exact.

## 3. A parent company's group policy

**What arrived.** `insurance v2.pdf`, in the world's second tenant: a certificate issued to "Foxley Holdings plc",
listing its subsidiaries under "Subsidiaries covered". Born digital.

**Matching.** The insured name matches the parent company, which is in scope because one of its subsidiaries is
engaged in this business unit (the runner-up name is 31 points behind). The covered names are resolved among the
parent's own subsidiaries only, so a group policy can never be stretched over an unrelated supplier with a similar
name.

```
insurance for engagement 30: COVERED_BY_PARENT (document 81)
  [metadata] received_before_start        PASS    received before the engagement started
             evidence record: first received 2026-06-02
  [field   ] ins_name_matches             PASS    'Foxley Holdings plc' matches 'Foxley Holdings plc' (100)
             evidence p.1: Insured: Foxley Holdings plc
  [field   ] ins_limit_meets_tier         PASS    limit EUR 4,000,000 meets EUR 2,000,000
             evidence p.1: General liability limit: 4.000.000 EUR any one occurrence
  [field   ] ins_cover_current            PASS    2027-07-25 is on or after 2026-10-01
             evidence p.1: Expiry date: 2027-07-25
  [field   ] ins_renewal_due              PASS    2027-07-25 is on or after 2026-10-31
             evidence p.1: Expiry date: 2027-07-25
  [field   ] ins_issued_recently          PASS    dated 2026-07-17, 76 days ago
             evidence p.1: Date of issue: July 17, 2026
```

The name check compares against the *insured party* (the parent), while the limit is compared against the tier of
the subsidiary's engagement. The status says how the requirement is met, COVERED_BY_PARENT, so a reviewer knows the
evidence is the group's, not the supplier's own. If the subsidiary later uploads its own certificate, the direct
document takes precedence.

## What did not happen

- No model call was made for any of these three documents: the rules decided everything.
- Re-uploading any of them would add an upload event and re-check only how and when it arrived; nothing would be
  extracted or judged again.
- Documents 7 and 44 belong to the first tenant and document 81 to the second. A session opened for one tenant
  cannot read the other's rows by any query, which is why the third command needs `--tenant 2`.
