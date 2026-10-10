"""Generate a world of insolvency processes with their claim documents and ground truth.

The world is adversarial on purpose. It includes:
- authorities that have lapsed, are about to, or let the representative file but not vote;
- documents issued by the wrong entity, and debenture trustees whose authority legitimately covers the holders;
- decoy pages that state an old value under a different label, and amendment pages that genuinely contradict page one;
- claims above the corporate debtor's books, amounts in words that disagree with the figures, defaults outside
  limitation, forms dated before the process was announced, broken GSTINs, PANs that are not the GSTIN's;
- charges held by someone else, created by another company, registered by an office that is no Registrar, or
  securing less than the facility;
- bank letters from unverified channels, with an IFSC of another bank, another account or another holder;
- agreements missing clauses, with hard-negative wordings and with text aimed at automated readers;
- duplicates, superseded versions, misfiled uploads, misleading filenames, documents of no known type;
- near-identical creditor names inside a process, and the same name in another tenant.

Text aimed at automated readers is printed at body size. An instruction meant for a machine is written to be read by
one; a 7-point line on a scanned page was destroyed by the scan in one run (OCR read "Not b s e esd o t st n ton about
it."), so no reader, human or automated, could act on it, and the world no longer plants one.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from ..ids import gstin_valid, ifsc_valid, lei_valid
from ..pack import Pack, load_pack
from ..textnorm import format_inr, rupees_in_words
from . import names as nm
from .clauses import ABSENT, BOILERPLATE, INJECTIONS, PRESENT
from .render import DocContent, render_pdf, to_photo_jpg, to_scan_pdf

AS_OF = date(2026, 10, 1)
SIZES = {"tiny": {"tenants": 2, "creditors": 5}, "small": {"tenants": 2, "creditors": 10},
         "full": {"tenants": 3, "creditors": 26}}
TENANTS = ["Saranya Insolvency Professionals LLP", "Prabodh Resolution Services Pvt Ltd",
           "Nyayadeep Restructuring Advisors LLP"]
MATTERS = [["Pench Valley Polymers Pvt Ltd", "Malwa Plateau Agro Foods Ltd", "Konkan Tidewater Marine Pvt Ltd"],
           ["Satpura Foundry Works Ltd", "Indrayani Spinners Pvt Ltd", "Vidarbha Cold Chain Ltd"],
           ["Chambal Ceramics Pvt Ltd", "Kalindi Auto Components Ltd", "Bhima River Paper Mills Ltd"]]
SECURED_FACILITIES = {"working_capital", "term_loan", "equipment_finance", "vehicle_finance"}
CRORE, LAKH = 10_000_000, 100_000
SANCTIONED = {"critical": [100, 150, 250], "high": [25, 40, 60, 75], "standard": [5, 8, 10, 15, 20],
              "low": [0.5, 1, 2, 3]}                       # crore
VOTING_POWERS = ["To attend and vote at meetings of the committee of creditors",
                 "To represent the creditor and vote in the committee of creditors",
                 "To attend, speak and vote for the creditor at every meeting of creditors"]
FILING_POWERS = ["To sign and submit the proof of claim and to receive notices",
                 "To file the claim and correspond with the resolution professional"]


class Tenant(BaseModel):
    id: int
    name: str


class Matter(BaseModel):
    id: int
    tenant_id: int
    name: str                                 # the corporate debtor
    debtor_cin: str
    commencement: date
    announced: date
    claims_due: date


class Creditor(BaseModel):
    id: int
    tenant_id: int
    legal_name: str
    address: str
    city: str
    cin: str
    pan: str
    gstin: str
    lei: str | None = None
    bank: str
    account_number: str
    ifsc: str
    category: str                             # the facility type it lends under
    trustee_id: int | None = None
    is_trustee: bool = False


class Claim(BaseModel):
    id: int
    tenant_id: int
    matter_id: int
    creditor_id: int
    tier: str
    claim_type: str
    facility_type: str
    book_balance: int
    sanctioned: int


class Attestation(BaseModel):
    tenant_id: int
    creditor_id: int
    requirement_id: str
    verified_by: str
    verified_on: date
    note: str


class UploadTruth(BaseModel):
    doc_type: str                         # a pack doc type, or "unknown"
    owner_creditor_id: int | None         # whose document it is (the trustee, for a trustee's authority)
    scenario_creditor_id: int | None      # the creditor this evidence was collected for
    covers: list[int] = Field(default_factory=list)
    expect: str = "match"                 # match | abstain | unknown: what a careful engine should do with it
    fields: dict[str, str] = Field(default_factory=dict)
    ambiguous: list[str] = Field(default_factory=list)
    injection: bool = False
    clauses: dict[str, bool] = Field(default_factory=dict)
    signed: bool | None = None
    duplicate_of: int | None = None
    format: str = "digital"
    variant: str = "normal"


class Upload(BaseModel):
    id: int
    tenant_id: int
    matter_id: int
    filename: str
    file: str
    channel: str
    uploaded_at: datetime
    declared_creditor_id: int | None = None
    truth: UploadTruth


class World(BaseModel):
    seed: int
    size: str
    as_of: date
    pack: str
    tenants: list[Tenant]
    matters: list[Matter]
    creditors: list[Creditor]
    claims: list[Claim]
    attestations: list[Attestation]
    uploads: list[Upload]

    def creditor(self, sid: int) -> Creditor:
        return next(s for s in self.creditors if s.id == sid)

    def matter(self, mid: int) -> Matter:
        return next(m for m in self.matters if m.id == mid)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _fmt_date(d: date, rng: random.Random) -> str:
    return rng.choice([d.strftime("%d.%m.%Y"), d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"), d.strftime("%d-%b-%Y"),
                       f"{_ordinal(d.day)} {d.strftime('%B, %Y')}", d.strftime("%d %B %Y")])


def _fmt_money(amount: int, rng: random.Random) -> str:
    choices = [format_inr(amount) + "/-", "INR " + format_inr(amount)[4:], f"Rs. {amount:,}", f"INR {amount:,}"]
    if amount % CRORE == 0:
        choices.append(f"Rs. {amount // CRORE} crore")
    elif amount % LAKH == 0:
        choices.append(f"Rs. {amount // LAKH} lakh")
    return rng.choice(choices)


class _Builder:
    def __init__(self, seed: int, size: str, out: Path, pack: Pack, digital_only: bool = False):
        self.rng = random.Random(seed)
        self.seed, self.size, self.out, self.pack = seed, size, out, pack
        self.digital_only = digital_only
        self.tenants: list[Tenant] = []
        self.matters: list[Matter] = []
        self.creditors: list[Creditor] = []
        self.claims: list[Claim] = []
        self.attestations: list[Attestation] = []
        self.uploads: list[Upload] = []
        self._ids = {"matter": 0, "creditor": 0, "claim": 0, "upload": 0}
        self._trustee_docs: dict[int, int] = {}

    def _next(self, kind: str) -> int:
        self._ids[kind] += 1
        return self._ids[kind]

    # ------------------------------------------------------------------ registry
    def _new_creditor(self, tenant_id: int, legal_name: str, category: str, **kw) -> Creditor:
        rng = self.rng
        city = nm.place(rng)
        bank = rng.choice(list(self.pack.bank_codes))
        pan = nm.pan(rng, legal_name)
        s = Creditor(id=self._next("creditor"), tenant_id=tenant_id, legal_name=legal_name,
                     address=nm.address(rng, city), city=city[0], cin=nm.cin(rng, legal_name, city[1]), pan=pan,
                     gstin=nm.gstin(city[2], pan), lei=nm.lei(rng) if rng.random() < 0.6 else None, bank=bank,
                     account_number=nm.account_number(rng), ifsc=nm.ifsc(rng, self.pack.bank_codes[bank]),
                     category=category, **kw)
        self.creditors.append(s)
        return s

    def _claim(self, s: Creditor, matter_id: int) -> Claim:
        rng = self.rng
        tier = rng.choices(["critical", "high", "standard", "low"], [0.15, 0.3, 0.4, 0.15])[0]
        sanctioned = int(rng.choice(SANCTIONED[tier]) * CRORE)
        secured = s.category in SECURED_FACILITIES and rng.random() < 0.85
        book = round(sanctioned * rng.uniform(0.55, 1.15), -3)
        e = Claim(id=self._next("claim"), tenant_id=s.tenant_id, matter_id=matter_id, creditor_id=s.id, tier=tier,
                  claim_type="secured" if secured else "unsecured", facility_type=s.category, book_balance=int(book),
                  sanctioned=sanctioned)
        self.claims.append(e)
        return e

    def build_registry(self) -> None:
        rng = self.rng
        cfg = SIZES[self.size]
        for t in range(cfg["tenants"]):
            tenant = Tenant(id=t + 1, name=TENANTS[t])
            self.tenants.append(tenant)
            matters = []
            for name in MATTERS[t]:
                commencement = AS_OF - timedelta(days=rng.randint(45, 240))
                city = nm.place(rng)
                matters.append(Matter(id=self._next("matter"), tenant_id=tenant.id, name=name,
                                      debtor_cin=nm.cin(rng, name, city[1]), commencement=commencement,
                                      announced=commencement + timedelta(days=rng.randint(1, 3)),
                                      claims_due=commencement + timedelta(days=14)))
            self.matters.extend(matters)
            used: set[str] = set()
            made = [self._new_creditor(tenant.id, nm.legal_name(rng, c, used), c)
                    for c in (rng.choice(list(nm.INDUSTRY)) for _ in range(cfg["creditors"]))]
            # debenture trustees: each acts for one or two of the creditors (the holders) under section 21(6A)(a)
            roots = rng.sample(nm.TRUSTEE_ROOTS, 2)
            for holder, root in zip(rng.sample(made, 2), roots, strict=True):
                trustee = self._new_creditor(tenant.id, f"{root} Trusteeship Services Ltd", holder.category,
                                             is_trustee=True)
                holder.trustee_id = trustee.id
                extra = next((m for m in made if m.trustee_id is None and m is not holder
                              and m.category == holder.category), None)
                if extra is not None and rng.random() < 0.5:
                    extra.trustee_id = trustee.id
            # near-identical names: a different company a careless matcher would confuse
            twins = []
            for orig in rng.sample(made, 2):
                twins.append((orig, self._new_creditor(tenant.id, nm.near_duplicate(orig.legal_name, rng),
                                                       orig.category)))
            # the same legal name as a creditor of the first tenant: never to be matched across tenants
            if t > 0:
                donor = rng.choice([s for s in self.creditors if s.tenant_id == 1 and not s.is_trustee])
                self._new_creditor(tenant.id, donor.legal_name, donor.category)
            # claims: one per creditor; a trustee acts for others and claims nothing itself
            for s in [x for x in self.creditors if x.tenant_id == tenant.id and not x.is_trustee]:
                self._claim(s, rng.choice(matters).id)
            # one twin pair claims in the same process (the hard case), the other in different ones
            (a, b), (c, d) = twins
            eb = next(e for e in self.claims if e.creditor_id == b.id)
            eb.matter_id = next(e for e in self.claims if e.creditor_id == a.id).matter_id
            ed = next(e for e in self.claims if e.creditor_id == d.id)
            ec = next(e for e in self.claims if e.creditor_id == c.id).matter_id
            if ed.matter_id == ec:
                ed.matter_id = next(m.id for m in matters if m.id != ec)

    # ------------------------------------------------------------------ documents
    def _claim_of(self, s: Creditor) -> Claim:
        return next(e for e in self.claims if e.creditor_id == s.id)

    def _matter_of(self, s: Creditor) -> Matter:
        mid = self._claim_of(s).matter_id
        return next(m for m in self.matters if m.id == mid)

    def _requirements_for(self, s: Creditor) -> list[str]:
        e = self._claim_of(s)
        return [rid for rid, spec in self.pack.requirements.items() if spec.applies_to.matches(e.tier, e.claim_type)]

    def _filename(self, alias: str, s: Creditor | None, fmt: str) -> str:
        rng = self.rng
        token = (s.legal_name.split(" ")[0] if s else "creditor").lower()
        stamp = f"2026{rng.randint(1, 9):02d}{rng.randint(1, 28):02d}"
        ext = ".jpg" if fmt == "photo" else ".pdf"
        base = rng.choice([f"SCN{rng.randint(1, 99999):05d}", f"IMG-{stamp}-WA{rng.randint(1, 9999):04d}",
                           f"Document ({rng.randint(1, 9)})", f"{alias}_final", f"{alias} v{rng.randint(2, 5)}",
                           f"{token}_{alias}", "attachment", f"{token} docs"])
        return base + ext

    def _write(self, upload_id: int, content: DocContent, fmt: str) -> str:
        pdf = render_pdf(content, self.rng)
        data = {"digital": lambda: pdf, "scan": lambda: to_scan_pdf(pdf, self.rng),
                "photo": lambda: to_photo_jpg(pdf, self.rng)}[fmt]()
        rel = f"files/{upload_id:05d}{'.jpg' if fmt == 'photo' else '.pdf'}"
        (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.out / rel).write_bytes(data)
        return rel

    def _add_upload(self, s: Creditor | None, tenant_id: int, doc_type: str, content: DocContent,
                    truth: UploadTruth, when: datetime, channel: str | None = None,
                    matter_id: int | None = None, declared: int | None = None, single_page: bool = True) -> Upload:
        rng = self.rng
        fmt = truth.format
        if fmt == "auto":
            if doc_type == "facility_agreement":
                fmt = "scan" if rng.random() < 0.15 else "digital"
            else:
                fmt = rng.choices(["digital", "scan", "photo"], [0.5, 0.35, 0.15 if single_page else 0.0])[0]
        if self.digital_only:
            fmt = "digital"
        truth.format = fmt
        uid = self._next("upload")
        rel = self._write(uid, content, fmt)
        if matter_id is None:
            matter_id = self._claim_of(s).matter_id if s is not None and not s.is_trustee else \
                rng.choice([m.id for m in self.matters if m.tenant_id == tenant_id])
        if channel is None:
            channel = rng.choices(["staff_upload", "creditor_portal", "email_ingest"], [0.55, 0.35, 0.1])[0]
        if channel == "creditor_portal" and declared is None and s is not None:
            declared = s.id
        aliases = self.pack.doc_types[doc_type].filename_aliases if doc_type in self.pack.doc_types else ["file"]
        if rng.random() < 0.1 and doc_type in self.pack.doc_types:   # a filename that suggests another type
            other = rng.choice([d for d in self.pack.doc_types if d != doc_type])
            aliases = self.pack.doc_types[other].filename_aliases
        up = Upload(id=uid, tenant_id=tenant_id, matter_id=matter_id,
                    filename=self._filename(rng.choice(aliases), s, fmt), file=rel, channel=channel,
                    uploaded_at=when, declared_creditor_id=declared, truth=truth)
        self.uploads.append(up)
        return up

    def _when(self, s: Creditor, early: bool = False, late_share: float = 0.15) -> datetime:
        """Documents arrive after the process is announced: most by the last date for claims, some later."""
        rng = self.rng
        m = self._matter_of(s)
        d = m.announced + timedelta(days=rng.randint(0, 4) if early else rng.randint(1, 13))
        if not early and rng.random() < late_share:
            d = m.claims_due + timedelta(days=rng.randint(1, 45))
        d = min(d, AS_OF - timedelta(days=1))
        return datetime.combine(d, time(rng.randint(8, 18), rng.randint(0, 59)))

    @staticmethod
    def _not_before(when: datetime, dated: date) -> datetime:
        """A document cannot arrive before the date it bears: a letter dated in August is uploaded in August."""
        return max(when, datetime.combine(dated + timedelta(days=1), when.time()))

    def world_creditor(self, sid: int | None) -> Creditor:
        return next(x for x in self.creditors if x.id == sid)

    def _stranger_name(self, s: Creditor) -> str:
        """A company name that is no creditor of this tenant: evidence issued by someone else."""
        from ..textnorm import name_similarity
        taken = [x.legal_name for x in self.creditors if x.tenant_id == s.tenant_id]
        for _ in range(200):
            cand = f"{self.rng.choice(nm.FIRST)} {self.rng.choice(nm.INDUSTRY[s.category])} Trading Pvt Ltd"
            if all(name_similarity(cand, t) < 85 for t in taken):
                return cand
        raise RuntimeError("no stranger name available")

    # authority ---------------------------------------------------------------
    def _authority(self, s: Creditor, variant: str, older: bool = False) -> Upload | None:
        rng = self.rng
        issuer = s
        covers: list[int] = []
        if variant == "trustee":
            trustee = self.world_creditor(s.trustee_id)
            if trustee.id in self._trustee_docs:
                return None
            issuer = trustee
            covers = [x.id for x in self.creditors if x.trustee_id == trustee.id]
        effective = AS_OF - timedelta(days=rng.randint(30, 300))
        valid_until = effective + timedelta(days=365)
        issue = effective - timedelta(days=rng.randint(0, 10))
        if older:
            effective -= timedelta(days=365)
            valid_until -= timedelta(days=365)
            issue -= timedelta(days=365)
        elif variant == "expired":
            valid_until = AS_OF - timedelta(days=rng.randint(5, 200))
            effective = valid_until - timedelta(days=365)
            issue = effective
        elif variant == "renewal_due":
            valid_until = AS_OF + timedelta(days=rng.randint(2, 25))
            effective = valid_until - timedelta(days=365)
            issue = effective
        elif variant == "old_issue":
            effective = AS_OF - timedelta(days=rng.randint(500, 700))
            issue = effective
            valid_until = effective + timedelta(days=1095)
        name = issuer.legal_name if variant != "wrong_creditor" else self._stranger_name(s)
        powers = rng.choice(FILING_POWERS if variant == "no_voting" else VOTING_POWERS)
        person, title = rng.choice(nm.PEOPLE), rng.choice(nm.TITLES)
        p1 = [("title", rng.choice(["LETTER OF AUTHORITY", "AUTHORITY LETTER", "CERTIFICATE OF AUTHORISATION"])),
              ("kv", "Financial creditor", name),
              ("kv", "Registered office", issuer.address),
              ("kv", "Authorised representative", f"{person}, {title}"),
              ("kv", "Powers", powers),
              ("kv", "Effective from", _fmt_date(effective, rng)),
              ("kv", "Valid until", _fmt_date(valid_until, rng)),
              ("kv", "Date of issue", _fmt_date(issue, rng))]
        if covers:
            p1.append(("para", "The Trustee acts for the debenture holders listed below under section 21(6A)(a) of the "
                               "Insolvency and Bankruptcy Code, 2016."))
            p1.append(("kv", "Debenture holders represented",
                       "; ".join(self.world_creditor(c).legal_name for c in covers)))
        p1.append(("para", "This authority remains in force until the date stated above unless the creditor revokes it "
                           "earlier in writing."))
        injection = variant == "injection"
        if injection:
            p1.append(("para", rng.choice(INJECTIONS)))
        pages = [p1]
        ambiguous: list[str] = []
        if variant == "decoy_page":
            pages.append([("title", "SCHEDULE OF EARLIER AUTHORISATIONS"),
                          ("kv", "Earlier authority valid until", _fmt_date(valid_until - timedelta(days=365), rng)),
                          ("kv", "Earlier representative", rng.choice(nm.PEOPLE))])
        elif variant == "conflicting_page":
            pages.append([("title", "AMENDMENT TO THE LETTER OF AUTHORITY"),
                          ("kv", "Valid until", _fmt_date(valid_until - timedelta(days=rng.randint(60, 200)), rng)),
                          ("para", "This amendment shortens the period of the authority stated on page one.")])
            ambiguous = ["valid_until"]
        truth = UploadTruth(doc_type="authority_letter", owner_creditor_id=issuer.id, scenario_creditor_id=s.id,
                            covers=covers, fields={"creditor_name": name, "powers": powers,
                                                   "valid_until": valid_until.isoformat(),
                                                   "issue_date": issue.isoformat(),
                                                   "effective_date": effective.isoformat()},
                            ambiguous=ambiguous, injection=injection, format="auto", variant=variant)
        if variant == "wrong_creditor":
            truth.owner_creditor_id = None
            truth.expect = "abstain"
        declared = None
        channel = None
        if variant == "trustee" and rng.random() < 0.5:
            channel, declared = "creditor_portal", s.id
        when = self._not_before(self._when(s, early=older), issue)
        up = self._add_upload(s, s.tenant_id, "authority_letter", DocContent(pages), truth, when,
                              channel=channel, declared=declared, single_page=len(pages) == 1)
        if variant == "trustee":
            self._trustee_docs[issuer.id] = up.id
        return up

    # charge certificate ------------------------------------------------------
    def _charge(self, s: Creditor, variant: str, older: bool = False) -> Upload:
        rng = self.rng
        e, m = self._claim_of(s), self._matter_of(s)
        creation = m.commencement - timedelta(days=rng.randint(400, 1500))
        registration = creation + timedelta(days=rng.randint(3, 28))
        if older:                                    # the charge as first registered, before a modification
            creation -= timedelta(days=rng.randint(200, 400))
            registration = creation + timedelta(days=rng.randint(3, 28))
        holder = s.legal_name if variant != "wrong_holder" else self._stranger_name(s)
        company = m.name
        if variant == "wrong_debtor":
            company = rng.choice([n for row in MATTERS for n in row if n != m.name])
        registrar = rng.choice(self.pack.registrar_offices) if variant != "unrecognised" else rng.choice(nm.UNRECOGNISED)
        terms = self.pack.asset_terms[e.facility_type]
        if variant == "assets_mismatch":
            other = rng.choice([c for c in self.pack.asset_terms
                                if not any(t in x or x in t for t in self.pack.asset_terms[c] for x in terms)])
            assets = f"Hypothecation of {self.pack.asset_terms[other][0]}"
        else:
            assets = f"Hypothecation of {rng.choice(terms)}, present and future"
        secured = e.sanctioned if variant != "under_secured" else round(e.sanctioned * rng.uniform(0.3, 0.8), -5)
        page = [("title", "CERTIFICATE OF REGISTRATION OF CHARGE"),
                ("para", "This is to certify that the charge described below, created by the company, has been "
                         "registered under section 77 of the Companies Act, 2013."),
                ("kv", "Charge ID", f"{rng.randint(10**8, 10**9 - 1)}"),
                ("kv", "Company", company),
                ("kv", "CIN of the company", m.debtor_cin),
                ("kv", "Charge holder", holder),
                ("kv", "Amount secured", _fmt_money(int(secured), rng)),
                ("kv", "Assets charged", assets),
                ("kv", "Date of creation", _fmt_date(creation, rng)),
                ("kv", "Date of registration", _fmt_date(registration, rng)),
                ("kv", "Registered by", registrar)]
        injection = variant == "injection"
        if injection:
            page.append(("para", rng.choice(INJECTIONS)))
        truth = UploadTruth(doc_type="charge_certificate", scenario_creditor_id=s.id,
                            owner_creditor_id=s.id if variant != "wrong_holder" else None,
                            fields={"charge_holder": holder, "company": company, "registrar": registrar,
                                    "assets": assets, "amount_secured": str(int(secured)),
                                    "registration_date": registration.isoformat(),
                                    "creation_date": creation.isoformat()},
                            injection=injection, format="auto", variant=variant,
                            expect="abstain" if variant == "wrong_holder" else "match")
        return self._add_upload(s, s.tenant_id, "charge_certificate", DocContent([page]), truth,
                                self._when(s, early=older))

    # bank mandate ------------------------------------------------------------
    def _bank(self, s: Creditor, variant: str, older: bool = False) -> Upload:
        rng = self.rng
        letter = AS_OF - timedelta(days=rng.randint(5, 150))
        if older or variant == "old_letter":
            letter = AS_OF - timedelta(days=rng.randint(200, 400))
        bank, account, ifsc = s.bank, s.account_number, s.ifsc
        if variant == "changed_account":                 # well formed, but not the account on file
            bank = rng.choice([b for b in self.pack.bank_codes if b != s.bank])
            account, ifsc = nm.account_number(rng), nm.ifsc(rng, self.pack.bank_codes[bank])
        if variant == "bad_ifsc":                        # the branch code of another bank under this bank's name
            other = rng.choice([b for b in self.pack.bank_codes if b != s.bank])
            ifsc = self.pack.bank_codes[other] + ifsc[4:]
        holder = s.legal_name if variant != "holder_mismatch" else \
            nm.split_form(s.legal_name)[0].split(" ")[0] + rng.choice([" Trading Pvt Ltd", " Payments Pvt Ltd",
                                                                      " Services India Ltd"])
        grouped = " ".join(account[i:i + 4] for i in range(0, len(account), 4)) if rng.random() < 0.3 else account
        page = [("title", rng.choice(["BANK ACCOUNT CONFIRMATION", "CONFIRMATION OF BANK DETAILS"])),
                ("kv", "Bank", bank), ("kv", "Branch", s.city), ("kv", "Date", _fmt_date(letter, rng)),
                ("para", "We confirm that the account below is maintained with us in the name shown."),
                ("kv", "Account holder", holder), ("kv", "Account number", grouped), ("kv", "IFSC", ifsc),
                ("para", "This letter is issued at the request of our customer and without any liability on the part "
                         "of the bank.")]
        channel = rng.choices(["staff_verified", "creditor_portal", "email_ingest"], [0.6, 0.3, 0.1])[0]
        ifsc_ok = ifsc_valid(ifsc) and ifsc[:4] == self.pack.bank_codes[bank]
        truth = UploadTruth(doc_type="bank_mandate", owner_creditor_id=s.id, scenario_creditor_id=s.id,
                            fields={"account_holder": holder, "account_number": account, "ifsc": ifsc,
                                    "letter_date": letter.isoformat(), "ifsc_valid": str(ifsc_ok),
                                    "account_on_file": str(account == s.account_number)},
                            format="auto", variant=variant)
        when = self._not_before(self._when(s, early=older), letter)
        return self._add_upload(s, s.tenant_id, "bank_mandate", DocContent([page]), truth, when, channel=channel)

    # proof of claim ----------------------------------------------------------
    def _claim_form(self, s: Creditor, variant: str) -> Upload:
        rng = self.rng
        e, m = self._claim_of(s), self._matter_of(s)
        name = s.legal_name if variant != "wrong_name" else nm.split_form(s.legal_name)[0].split(" ")[0] + \
            " Former Trading Pvt Ltd"
        gstin, pan, lei = s.gstin, s.pan, s.lei
        if variant == "bad_gstin":                    # a typo in the check character
            gstin = gstin[:-1] + rng.choice([c for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c != gstin[-1]])
        if variant == "pan_mismatch":                 # the PAN of another entity
            pan = pan[:5] + f"{(int(pan[5:9]) + rng.randint(1, 5000)) % 10000:04d}" + pan[9]
        if lei and variant == "bad_lei":
            lei = lei[:-2] + f"{(int(lei[-2:]) + rng.randint(1, 50)) % 100:02d}"
        amount = round(e.book_balance * rng.uniform(0.97, 1.015), -3)
        if variant == "over_books":
            amount = round(e.book_balance * rng.uniform(1.08, 1.4), -3)
        amount = int(amount)
        words_amount = amount
        if variant == "words_mismatch":
            words_amount = amount + rng.choice([-1, 1]) * rng.choice([LAKH, 10 * LAKH, CRORE])
            words_amount = words_amount if words_amount > 0 else amount + CRORE
        default = m.commencement - timedelta(days=rng.randint(60, 3 * 365 - 30))
        if variant == "time_barred":
            default = m.commencement - timedelta(days=rng.randint(3 * 365 + 20, 5 * 365))
        when = self._when(s)
        claim_date = max(m.announced, when.date() - timedelta(days=rng.randint(0, 4)))
        if variant == "early_dated":
            claim_date = m.announced - timedelta(days=rng.randint(10, 60))
        p1 = [("title", "FORM C"),
              ("para", "PROOF OF CLAIM BY FINANCIAL CREDITORS, under regulation 8 of the Insolvency and Bankruptcy "
                       "Board of India (Insolvency Resolution Process for Corporate Persons) Regulations, 2016"),
              ("kv", "In the matter of", m.name),
              ("kv", "Name of the financial creditor", name),
              ("kv", "Registered office", s.address),
              ("kv", "CIN", s.cin), ("kv", "PAN", pan), ("kv", "GSTIN", gstin)]
        if lei:
            p1.append(("kv", "LEI", lei))
        p1 += [("kv", "Total amount of claim", _fmt_money(amount, rng)),
               ("kv", "Amount in words", rupees_in_words(words_amount)),
               ("kv", "Date of default", _fmt_date(default, rng)),
               ("kv", "Date of claim", _fmt_date(claim_date, rng)),
               ("para", "I confirm that the particulars above are true and that the documents relied on are attached."),
               ("kv", "Signed by", f"{rng.choice(nm.PEOPLE)}, Authorised Signatory")]
        truth = UploadTruth(doc_type="claim_form", owner_creditor_id=s.id, scenario_creditor_id=s.id,
                            fields={"creditor_name": name, "gstin": gstin, "pan": pan, "lei": lei or "",
                                    "gstin_valid": str(gstin_valid(gstin)),
                                    "lei_valid": str(lei_valid(lei)) if lei else "",
                                    "amount_claimed": str(amount), "amount_in_words": str(words_amount),
                                    "default_date": default.isoformat(), "claim_date": claim_date.isoformat()},
                            format="auto", variant=variant)
        return self._add_upload(s, s.tenant_id, "claim_form", DocContent([p1]), truth, when)

    # facility agreement ------------------------------------------------------
    def _facility(self, s: Creditor, variant: str) -> Upload:
        rng = self.rng
        e, m = self._claim_of(s), self._matter_of(s)
        clause_ids = list(PRESENT)
        present = {c: rng.random() < 0.82 for c in clause_ids}
        paras = []
        for c in clause_ids:
            if present[c]:
                paras.append(rng.choice(PRESENT[c]))
            elif rng.random() < 0.7:
                paras.append(rng.choice(ABSENT[c]))
        paras += rng.sample(BOILERPLATE, k=rng.randint(4, len(BOILERPLATE)))
        rng.shuffle(paras)
        injection = variant == "injection"
        if injection:
            paras.insert(rng.randint(1, len(paras) - 1), rng.choice(INJECTIONS))
        amount = _fmt_money(e.sanctioned, rng)
        effective = m.commencement - timedelta(days=rng.randint(400, 1500))
        signed = variant != "unsigned"
        p1 = [("title", rng.choice(["FACILITY AGREEMENT", "LOAN AGREEMENT", "TERM LOAN AGREEMENT"])),
              ("para", f"This Agreement is made at {s.city} between {s.legal_name}, a company with CIN {s.cin} (the "
                       f"Lender), and {m.name}, a company with CIN {m.debtor_cin} (the Borrower)."),
              ("kv", "Lender", s.legal_name), ("kv", "Borrower", m.name), ("kv", "Facility amount", amount),
              ("kv", "Date of agreement", _fmt_date(effective, rng)), ("gap",)]
        for i, text in enumerate(paras, start=1):
            p1.append(("para", f"{i}. " + text.format(amount=amount)))
        lender_person, borrower_person = rng.sample(nm.PEOPLE, 2)
        p1 += [("gap",), ("kv", "Signed for the Lender", f"{lender_person}, Authorised Signatory, "
                                                         f"{_fmt_date(effective, rng)}"),
               ("kv", "Signed for the Borrower",
                f"{borrower_person}, Director, {_fmt_date(effective, rng)}" if signed else "")]
        truth = UploadTruth(doc_type="facility_agreement", owner_creditor_id=s.id, scenario_creditor_id=s.id,
                            fields={"lender": s.legal_name}, injection=injection, clauses=present, signed=signed,
                            format="auto", variant=variant)
        return self._add_upload(s, s.tenant_id, "facility_agreement", DocContent([p1]), truth, self._when(s),
                                channel=rng.choice(["staff_upload", "staff_upload", "email_ingest"]), single_page=False)

    def _unknown(self, tenant: Tenant) -> Upload:
        rng = self.rng
        if rng.random() < 0.5:
            page = [("title", "COMPANY PROFILE"),
                    ("para", "Our team brings decades of experience to every client. We pride ourselves on service, "
                             "reliability and value, and we look forward to working with you."),
                    ("para", "Contact our relationship team to arrange a meeting.")]
        else:
            page = [("title", "MINUTES OF THE MEETING OF THE COMMITTEE OF CREDITORS"),
                    ("kv", "Attendees", ", ".join(rng.sample(nm.PEOPLE, 3))),
                    ("para", "The committee noted the updated list of creditors and agreed to meet again in two weeks.")]
        truth = UploadTruth(doc_type="unknown", owner_creditor_id=None, scenario_creditor_id=None, expect="unknown",
                            format=rng.choice(["digital", "scan"]))
        when = datetime.combine(AS_OF - timedelta(days=rng.randint(5, 40)), time(10, 0))
        return self._add_upload(None, tenant.id, "unknown", DocContent([page]), truth, when, channel="email_ingest")

    VARIANTS = {
        "authority": (["normal", "expired", "renewal_due", "no_voting", "wrong_creditor", "decoy_page",
                       "conflicting_page", "old_issue", "injection"],
                      [0.44, 0.08, 0.08, 0.08, 0.06, 0.08, 0.06, 0.06, 0.06]),
        "security_interest": (["normal", "wrong_holder", "wrong_debtor", "unrecognised", "assets_mismatch",
                               "under_secured", "injection"],
                              [0.52, 0.06, 0.08, 0.1, 0.1, 0.08, 0.06]),
        "bank_details": (["normal", "bad_ifsc", "holder_mismatch", "changed_account", "old_letter"],
                         [0.64, 0.09, 0.09, 0.09, 0.09]),
        "proof_of_claim": (["normal", "wrong_name", "bad_gstin", "pan_mismatch", "bad_lei", "over_books",
                            "words_mismatch", "time_barred", "early_dated"],
                           [0.44, 0.07, 0.07, 0.07, 0.07, 0.08, 0.07, 0.07, 0.06]),
        "evidence_of_debt": (["normal", "unsigned", "injection"], [0.82, 0.1, 0.08]),
    }

    def build_documents(self) -> None:
        rng = self.rng
        for tenant in self.tenants:
            for s in [x for x in self.creditors if x.tenant_id == tenant.id and not x.is_trustee]:
                for rid in self._requirements_for(s):
                    if rid == "bank_details" and rng.random() < 0.06:
                        self.attestations.append(Attestation(
                            tenant_id=tenant.id, creditor_id=s.id, requirement_id=rid,
                            verified_by=rng.choice(nm.PEOPLE), verified_on=AS_OF - timedelta(days=rng.randint(5, 60)),
                            note="Account details confirmed by call-back to the number on file."))
                        continue
                    if rng.random() < 0.08:
                        continue                                       # missing
                    variants, weights = self.VARIANTS[rid]
                    variant = rng.choices(variants, weights)[0]
                    if rid == "authority":
                        if s.trustee_id and rng.random() < 0.5:
                            variant = "trustee"
                        if variant != "trustee" and rng.random() < 0.1:
                            self._authority(s, "normal", older=True)
                        self._authority(s, variant)
                    elif rid == "security_interest":
                        if rng.random() < 0.1:
                            self._charge(s, "normal", older=True)
                        self._charge(s, variant)
                    elif rid == "bank_details":
                        self._bank(s, variant)
                    elif rid == "proof_of_claim":
                        self._claim_form(s, variant)
                    elif rid == "evidence_of_debt":
                        self._facility(s, variant)
            for _ in range(max(1, len(self.uploads) // 40)):
                self._unknown(tenant)
        self._noise()

    def _noise(self) -> None:
        """Duplicates, misfiled uploads and a cross-tenant stray."""
        rng = self.rng
        originals = [u for u in self.uploads if u.truth.doc_type != "unknown"]
        for u in rng.sample(originals, k=max(2, len(originals) // 12)):
            uid = self._next("upload")
            rel = f"files/{uid:05d}{Path(u.file).suffix}"
            (self.out / rel).write_bytes((self.out / u.file).read_bytes())
            truth = u.truth.model_copy(deep=True)
            truth.duplicate_of = u.id
            self.uploads.append(Upload(id=uid, tenant_id=u.tenant_id, matter_id=u.matter_id,
                                       filename=f"Copy of {u.filename}", file=rel, channel="email_ingest",
                                       uploaded_at=u.uploaded_at + timedelta(days=rng.randint(1, 20)),
                                       declared_creditor_id=None, truth=truth))
        # misfiled: uploaded to a process in which the creditor has no claim; at least one carries only a name
        pool = [u for u in originals if u.truth.expect == "match" and u.truth.duplicate_of is None
                and u.declared_creditor_id is None]
        picked = rng.sample(pool, k=min(len(pool), max(1, len(originals) // 25)))
        named_only = [u for u in pool if u.truth.doc_type in ("authority_letter", "charge_certificate")
                      and not u.truth.covers]
        if named_only and not any(u in named_only for u in picked):
            picked.append(rng.choice(named_only))
        for u in picked:
            owner = u.truth.owner_creditor_id
            acting_for = {owner} | {c.id for c in self.creditors if c.trustee_id == owner}
            in_scope = {e.matter_id for e in self.claims if e.creditor_id in acting_for}   # a trustee: its holders'
            others = [m.id for m in self.matters if m.tenant_id == u.tenant_id and m.id not in in_scope]
            if not others or u.declared_creditor_id is not None:
                continue
            u.matter_id = rng.choice(others)
            u.truth.variant += "+misfiled"
            # identifiers on the page let a careful engine place it anyway; a name alone does not
            if u.truth.doc_type not in ("bank_mandate", "claim_form", "facility_agreement"):
                u.truth.expect = "abstain"
        # a document sent to the wrong tenant: it names a creditor that tenant does not have
        if len(self.tenants) > 1:
            u = rng.choice([x for x in originals if x.tenant_id == 1 and x.truth.doc_type == "claim_form"])
            uid = self._next("upload")
            rel = f"files/{uid:05d}{Path(u.file).suffix}"
            (self.out / rel).write_bytes((self.out / u.file).read_bytes())
            truth = u.truth.model_copy(deep=True)
            truth.owner_creditor_id, truth.scenario_creditor_id, truth.expect = None, None, "abstain"
            truth.variant = "wrong_tenant"
            t2 = self.tenants[1]
            self.uploads.append(Upload(id=uid, tenant_id=t2.id,
                                       matter_id=next(m.id for m in self.matters if m.tenant_id == t2.id),
                                       filename=u.filename, file=rel, channel="email_ingest",
                                       uploaded_at=u.uploaded_at, truth=truth))
        self.uploads.sort(key=lambda x: (x.uploaded_at, x.id))


def build_world(seed: int = 7, size: str = "small", out: str | Path = "data/world", digital_only: bool = False) -> World:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pack = load_pack()
    b = _Builder(seed, size, out, pack, digital_only)
    b.build_registry()
    b.build_documents()
    world = World(seed=seed, size=size, as_of=AS_OF, pack=pack.digest, tenants=b.tenants, matters=b.matters,
                  creditors=b.creditors, claims=b.claims, attestations=b.attestations, uploads=b.uploads)
    (out / "world.json").write_text(world.model_dump_json(indent=1), encoding="utf-8")
    return world


def load_world(path: str | Path) -> World:
    return World.model_validate_json((Path(path) / "world.json").read_text(encoding="utf-8"))
