"""Generate a supplier world with its evidence files and ground truth.

The world is adversarial on purpose. It includes:
- expired, about-to-lapse and under-insured certificates;
- certificates for the wrong entity, and group policies that legitimately cover subsidiaries;
- decoy pages that state an old value under a different label;
- endorsement pages that genuinely contradict page one;
- bank letters from unverified channels, with broken check digits or a different account holder;
- contracts missing clauses, with hard-negative wordings and with text aimed at automated readers;
- duplicates, superseded versions, misfiled uploads, misleading filenames, documents of no known type;
- near-identical supplier names inside a tenant, and the same name in another tenant.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from ..ids import format_iban, iban_valid, lei_valid
from ..pack import Pack, load_pack
from . import names as nm
from .clauses import ABSENT, BOILERPLATE, INJECTIONS, PRESENT
from .render import DocContent, render_pdf, to_photo_jpg, to_scan_pdf

AS_OF = date(2026, 10, 1)
SIZES = {"tiny": {"tenants": 2, "suppliers": 5}, "small": {"tenants": 2, "suppliers": 10},
         "full": {"tenants": 3, "suppliers": 26}}
TENANTS = ["Larkspur Retail Group", "Halden Health Partners", "Orrin Financial Services"]
BUS = [["Northern Stores", "Southern Stores", "Head Office"],
       ["City Hospitals", "Community Care", "Shared Services"],
       ["Retail Banking", "Wealth Management", "Group Operations"]]
PEOPLE = ["Anika Rao", "Tomas Brandt", "Grace Okafor", "Liam Duarte", "Mei Lin", "Oskar Varga", "Priya Nair",
          "Samuel Achterberg", "Ines Moreau", "Kofi Mensah", "Hannah Kowalski", "Rafael Ortiz"]
DATA_CATEGORIES = {"cloud_hosting", "payroll_services", "software_development"}


class Tenant(BaseModel):
    id: int
    name: str


class BusinessUnit(BaseModel):
    id: int
    tenant_id: int
    name: str


class Supplier(BaseModel):
    id: int
    tenant_id: int
    legal_name: str
    address: str
    company_number: str
    vat_number: str
    lei: str | None = None
    bank: str
    iban: str
    bic: str
    category: str
    parent_id: int | None = None
    is_parent: bool = False


class Engagement(BaseModel):
    id: int
    tenant_id: int
    business_unit_id: int
    supplier_id: int
    tier: str
    engagement_type: str
    service_category: str
    start_date: date
    end_date: date


class Attestation(BaseModel):
    tenant_id: int
    supplier_id: int
    requirement_id: str
    verified_by: str
    verified_on: date
    note: str


class UploadTruth(BaseModel):
    doc_type: str                         # a pack doc type, or "unknown"
    owner_supplier_id: int | None         # whose document it is (the insured party for insurance)
    scenario_supplier_id: int | None      # the supplier this evidence was collected for
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
    business_unit_id: int
    filename: str
    file: str
    channel: str
    uploaded_at: datetime
    claimed_supplier_id: int | None = None
    truth: UploadTruth


class World(BaseModel):
    seed: int
    size: str
    as_of: date
    pack: str
    tenants: list[Tenant]
    business_units: list[BusinessUnit]
    suppliers: list[Supplier]
    engagements: list[Engagement]
    attestations: list[Attestation]
    uploads: list[Upload]

    def supplier(self, sid: int) -> Supplier:
        return next(s for s in self.suppliers if s.id == sid)


def _fmt_date(d: date, rng: random.Random) -> str:
    return rng.choice([d.strftime("%d %B %Y"), d.strftime("%d/%m/%Y"), d.isoformat(), d.strftime("%B %d, %Y")])


def _fmt_money(amount: int, rng: random.Random) -> str:
    choices = [f"EUR {amount:,}", f"€{amount:,}", f"{amount:,} EUR".replace(",", ".")]
    if amount % 1_000_000 == 0:
        choices.append(f"EUR {amount // 1_000_000}m")
    return rng.choice(choices)


class _Builder:
    def __init__(self, seed: int, size: str, out: Path, pack: Pack, digital_only: bool = False):
        self.rng = random.Random(seed)
        self.seed, self.size, self.out, self.pack = seed, size, out, pack
        self.digital_only = digital_only
        self.tenants: list[Tenant] = []
        self.bus: list[BusinessUnit] = []
        self.suppliers: list[Supplier] = []
        self.engagements: list[Engagement] = []
        self.attestations: list[Attestation] = []
        self.uploads: list[Upload] = []
        self._ids = {"bu": 0, "supplier": 0, "engagement": 0, "upload": 0}
        self._group_docs: dict[int, int] = {}

    def _next(self, kind: str) -> int:
        self._ids[kind] += 1
        return self._ids[kind]

    # ------------------------------------------------------------------ registry
    def _new_supplier(self, tenant_id: int, legal_name: str, category: str, **kw) -> Supplier:
        rng = self.rng
        bank = rng.choice(nm.BANKS)
        s = Supplier(id=self._next("supplier"), tenant_id=tenant_id, legal_name=legal_name, address=nm.address(rng),
                     company_number=nm.company_number(rng), vat_number=nm.vat_number(rng),
                     lei=nm.lei(rng) if rng.random() < 0.6 else None, bank=bank, iban=nm.iban(rng, bank),
                     bic=nm.bic(bank), category=category, **kw)
        self.suppliers.append(s)
        return s

    def _engage(self, s: Supplier, bu_id: int) -> Engagement:
        rng = self.rng
        start = AS_OF + timedelta(days=rng.randint(-420, 45))
        tier = rng.choices(["critical", "high", "standard", "low"], [0.15, 0.3, 0.4, 0.15])[0]
        etype = "data_processing" if (s.category in DATA_CATEGORIES and rng.random() < 0.8) else "services"
        e = Engagement(id=self._next("engagement"), tenant_id=s.tenant_id, business_unit_id=bu_id, supplier_id=s.id,
                       tier=tier, engagement_type=etype, service_category=s.category, start_date=start,
                       end_date=start + timedelta(days=rng.randint(300, 1100)))
        self.engagements.append(e)
        return e

    def build_registry(self) -> None:
        rng = self.rng
        cfg = SIZES[self.size]
        for t in range(cfg["tenants"]):
            tenant = Tenant(id=t + 1, name=TENANTS[t])
            self.tenants.append(tenant)
            bus = [BusinessUnit(id=self._next("bu"), tenant_id=tenant.id, name=n) for n in BUS[t]]
            self.bus.extend(bus)
            used: set[str] = set()
            made = [self._new_supplier(tenant.id, nm.legal_name(rng, c, used), c)
                    for c in (rng.choice(list(nm.INDUSTRY)) for _ in range(cfg["suppliers"]))]
            # two corporate groups: a parent holding company and one or two subsidiaries carrying its brand
            for sub in rng.sample(made, 2):
                first = sub.legal_name.split(" ")[0]
                if any(x.legal_name == f"{first} Holdings plc" for x in self.suppliers if x.tenant_id == tenant.id):
                    continue
                parent = self._new_supplier(tenant.id, f"{first} Holdings plc", sub.category, is_parent=True)
                sub.parent_id = parent.id
                extra = next((m for m in made if m.parent_id is None and not m.is_parent and m is not sub
                              and m.category == sub.category), None)
                if extra is not None and rng.random() < 0.5:
                    extra.parent_id = parent.id
                    renamed = f"{first} {extra.legal_name.split(' ', 1)[1]}"
                    if all(renamed != x.legal_name for x in self.suppliers if x.tenant_id == tenant.id):
                        extra.legal_name = renamed
            # near-identical names: a different company a careless matcher would confuse
            twins = []
            for orig in rng.sample([m for m in made if not m.is_parent], 2):
                twins.append((orig, self._new_supplier(tenant.id, nm.near_duplicate(orig.legal_name, rng), orig.category)))
            # the same legal name as a supplier of the first tenant: never to be matched across tenants
            if t > 0:
                donor = rng.choice([s for s in self.suppliers if s.tenant_id == 1 and not s.is_parent])
                self._new_supplier(tenant.id, donor.legal_name, donor.category)
            # engagements
            for s in [x for x in self.suppliers if x.tenant_id == tenant.id]:
                if s.is_parent and rng.random() < 0.5:
                    continue
                first_bu = rng.choice(bus)
                self._engage(s, first_bu.id)
                if rng.random() < 0.25:
                    self._engage(s, rng.choice([b for b in bus if b.id != first_bu.id]).id)
            # one twin pair shares a business unit (the hard case), the other is split across units
            (a, b), (c, d) = twins
            eb = next(e for e in self.engagements if e.supplier_id == b.id)
            eb.business_unit_id = next(e for e in self.engagements if e.supplier_id == a.id).business_unit_id
            ed = next(e for e in self.engagements if e.supplier_id == d.id)
            ec_bu = next(e for e in self.engagements if e.supplier_id == c.id).business_unit_id
            if ed.business_unit_id == ec_bu:
                ed.business_unit_id = next(x.id for x in bus if x.id != ec_bu)

    # ------------------------------------------------------------------ documents
    def _engagements_of(self, s: Supplier) -> list[Engagement]:
        return [e for e in self.engagements if e.supplier_id == s.id]

    def _requirements_for(self, s: Supplier) -> list[str]:
        reqs = []
        for rid, spec in self.pack.requirements.items():
            if any(spec.applies_to.matches(e.tier, e.engagement_type) for e in self._engagements_of(s)):
                reqs.append(rid)
        return reqs

    def _filename(self, alias: str, s: Supplier | None, fmt: str) -> str:
        rng = self.rng
        token = (s.legal_name.split(" ")[0] if s else "supplier").lower()
        stamp = f"2026{rng.randint(1, 9):02d}{rng.randint(1, 28):02d}_{rng.randint(80000, 175959):06d}"
        ext = ".jpg" if fmt == "photo" else ".pdf"
        base = rng.choice([f"scan_{rng.randint(1, 9999):04d}", f"IMG_{stamp}", f"Document ({rng.randint(1, 9)})",
                           f"{alias}_final", f"{alias} v{rng.randint(2, 5)}", f"{token}_{alias}", "attachment",
                           f"{token} docs"])
        return base + ext

    def _write(self, upload_id: int, content: DocContent, fmt: str, raw: bytes | None = None) -> tuple[str, bytes]:
        pdf = render_pdf(content, self.rng) if raw is None else raw
        data = {"digital": lambda: pdf, "scan": lambda: to_scan_pdf(pdf, self.rng),
                "photo": lambda: to_photo_jpg(pdf, self.rng)}[fmt]()
        rel = f"files/{upload_id:05d}{'.jpg' if fmt == 'photo' else '.pdf'}"
        (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.out / rel).write_bytes(data)
        return rel, data

    def _add_upload(self, s: Supplier | None, tenant_id: int, doc_type: str, content: DocContent,
                    truth: UploadTruth, when: datetime, channel: str | None = None,
                    bu_id: int | None = None, claimed: int | None = None, single_page: bool = True) -> Upload:
        rng = self.rng
        fmt = truth.format
        if fmt == "auto":
            if doc_type == "master_agreement":
                fmt = "scan" if rng.random() < 0.15 else "digital"
            else:
                fmt = rng.choices(["digital", "scan", "photo"], [0.5, 0.35, 0.15 if single_page else 0.0])[0]
        if self.digital_only:
            fmt = "digital"
        truth.format = fmt
        uid = self._next("upload")
        rel, _ = self._write(uid, content, fmt)
        engs = self._engagements_of(s) if s else []
        if bu_id is None:
            bu_id = rng.choice(engs).business_unit_id if engs else \
                rng.choice([b.id for b in self.bus if b.tenant_id == tenant_id])
        if channel is None:
            channel = rng.choices(["staff_upload", "supplier_portal", "email_ingest"], [0.55, 0.35, 0.1])[0]
        if channel == "supplier_portal" and claimed is None and s is not None:
            claimed = s.id
        aliases = self.pack.doc_types[doc_type].filename_aliases if doc_type in self.pack.doc_types else ["file"]
        if rng.random() < 0.1 and doc_type in self.pack.doc_types:   # a filename that suggests another type
            other = rng.choice([d for d in self.pack.doc_types if d != doc_type])
            aliases = self.pack.doc_types[other].filename_aliases
        up = Upload(id=uid, tenant_id=tenant_id, business_unit_id=bu_id, filename=self._filename(rng.choice(aliases), s, fmt),
                    file=rel, channel=channel, uploaded_at=when, claimed_supplier_id=claimed, truth=truth)
        self.uploads.append(up)
        return up

    def _when(self, s: Supplier, early: bool = False) -> datetime:
        rng = self.rng
        start = min(e.start_date for e in self._engagements_of(s))
        d = start - timedelta(days=rng.randint(60, 400) if early else rng.randint(1, 60))
        if not early and rng.random() < 0.15:
            d = start + timedelta(days=rng.randint(1, 120))
        d = min(d, AS_OF - timedelta(days=1))
        return datetime.combine(d, time(rng.randint(8, 18), rng.randint(0, 59)))

    # insurance ---------------------------------------------------------------
    def _insurance(self, s: Supplier, variant: str, tenant: Tenant, older: bool = False) -> Upload | None:
        rng = self.rng
        engs = self._engagements_of(s)
        need = max(self.pack.tier_minimum_liability[e.tier] for e in engs)
        insured = s
        covers: list[int] = []
        if variant == "group":
            parent = self.world_supplier(s.parent_id)
            if parent.id in self._group_docs:
                return None
            insured = parent
            covers = [x.id for x in self.suppliers if x.parent_id == parent.id]
            need = max([self.pack.tier_minimum_liability[e.tier] for x in covers
                        for e in self.engagements if e.supplier_id == x] + [need])
        limit = need * rng.choice([1, 2, 3]) if variant != "low_limit" else max(
            500_000, need // rng.choice([2, 4, 5]))
        effective = AS_OF - timedelta(days=rng.randint(30, 300))
        expiry = effective + timedelta(days=365)
        issue = effective - timedelta(days=rng.randint(0, 10))
        if older:
            effective -= timedelta(days=365)
            expiry -= timedelta(days=365)
            issue -= timedelta(days=365)
        elif variant == "expired":
            expiry = AS_OF - timedelta(days=rng.randint(5, 200))
            effective = expiry - timedelta(days=365)
            issue = effective
        elif variant == "renewal_due":
            expiry = AS_OF + timedelta(days=rng.randint(2, 25))
            effective = expiry - timedelta(days=365)
            issue = effective
        elif variant == "old_issue":
            effective = AS_OF - timedelta(days=rng.randint(500, 700))
            issue = effective
            expiry = effective + timedelta(days=1095)
        insured_name = insured.legal_name
        if variant == "wrong_insured":
            insured_name = self._stranger_name(s)
        p1 = [("title", rng.choice(["CERTIFICATE OF INSURANCE", "EVIDENCE OF INSURANCE", "CONFIRMATION OF COVER"])),
              ("kv", "Insurer", rng.choice(nm.INSURERS)),
              ("kv", "Policy number", f"GL-{issue.year}-{rng.randint(100000, 999999)}"),
              ("kv", rng.choice(["Named insured", "Insured", "Policyholder"]), insured_name),
              ("kv", "Registered address", insured.address),
              ("kv", "General liability limit", _fmt_money(limit, rng) + " any one occurrence"),
              ("kv", "Effective date", _fmt_date(effective, rng)),
              ("kv", "Expiry date", _fmt_date(expiry, rng)),
              ("kv", "Date of issue", _fmt_date(issue, rng)),
              ("kv", "Certificate holder", tenant.name)]
        if covers:
            p1.append(("kv", "Subsidiaries covered", "; ".join(self.world_supplier(c).legal_name for c in covers)))
        p1.append(("para", "This certificate is issued as a matter of information only. It confers no rights on the "
                           "certificate holder and does not amend, extend or alter the cover of the policy."))
        injection = variant == "injection"
        if injection:
            p1.append(("small", rng.choice(INJECTIONS)))
        pages = [p1]
        ambiguous: list[str] = []
        if variant == "decoy_page":
            pages.append([("title", "SCHEDULE OF PRIOR COVER"),
                          ("kv", "Prior period expiry date", _fmt_date(expiry - timedelta(days=365), rng)),
                          ("kv", "Prior period limit", _fmt_money(max(500_000, limit // 4), rng))])
        elif variant == "conflicting_page":
            pages.append([("title", "ENDORSEMENT"),
                          ("kv", "Expiry date", _fmt_date(expiry - timedelta(days=rng.randint(60, 200)), rng)),
                          ("para", "This endorsement amends the period of insurance stated on the schedule.")])
            ambiguous = ["expiry_date"]
        truth = UploadTruth(doc_type="insurance_certificate", owner_supplier_id=insured.id, scenario_supplier_id=s.id,
                            covers=covers, fields={"insured_name": insured_name, "gl_limit": str(limit),
                                                   "expiry_date": expiry.isoformat(), "issue_date": issue.isoformat(),
                                                   "effective_date": effective.isoformat()},
                            ambiguous=ambiguous, injection=injection, format="auto", variant=variant)
        if variant == "wrong_insured":
            truth.owner_supplier_id = None
            truth.expect = "abstain"
        claimed = None
        channel = None
        if variant == "group" and rng.random() < 0.5:
            channel, claimed = "supplier_portal", s.id
        up = self._add_upload(s, s.tenant_id, "insurance_certificate", DocContent(pages), truth,
                              self._when(s, early=older), channel=channel, claimed=claimed,
                              single_page=len(pages) == 1)
        if variant == "group":
            self._group_docs[insured.id] = up.id
        return up

    def world_supplier(self, sid: int | None) -> Supplier:
        return next(x for x in self.suppliers if x.id == sid)

    def _stranger_name(self, s: Supplier) -> str:
        """A company name that is not any supplier of this tenant: evidence issued to someone else."""
        from ..textnorm import name_similarity
        taken = [x.legal_name for x in self.suppliers if x.tenant_id == s.tenant_id]
        for _ in range(200):
            cand = f"{self.rng.choice(nm.FIRST)} {self.rng.choice(nm.INDUSTRY[s.category])} Trading Ltd"
            if all(name_similarity(cand, t) < 85 for t in taken):
                return cand
        raise RuntimeError("no stranger name available")

    # security certificate ----------------------------------------------------
    def _security(self, s: Supplier, variant: str, older: bool = False) -> Upload:
        rng = self.rng
        issue = AS_OF - timedelta(days=rng.randint(30, 900))
        expiry = issue + timedelta(days=1095)
        if older:
            issue -= timedelta(days=1095)
            expiry -= timedelta(days=1095)
        elif variant == "expired":
            expiry = AS_OF - timedelta(days=rng.randint(10, 300))
            issue = expiry - timedelta(days=1095)
        org = s.legal_name if variant != "wrong_org" else self._stranger_name(s)
        body = rng.choice(self.pack.accredited_bodies) if variant != "unaccredited" else rng.choice(nm.UNACCREDITED)
        terms = self.pack.service_terms[s.category]
        if variant == "scope_mismatch":
            other = rng.choice([c for c in self.pack.service_terms if c != s.category])
            scope = f"Provision of {self.pack.service_terms[other][0]} services"
        else:
            scope = f"Provision of {rng.choice(terms)} services to business customers"
        page = [("title", "CERTIFICATE OF REGISTRATION"),
                ("para", "This is to certify that the information security management system of the organisation "
                         "below has been assessed and found to conform to the standard stated."),
                ("kv", "Organisation", org), ("kv", "Certificate number", f"ISMS-{rng.randint(100000, 999999)}"),
                ("kv", "Standard", "ISO/IEC 27001:2022"), ("kv", "Scope", scope),
                ("kv", "Certification body", body), ("kv", "Date of issue", _fmt_date(issue, rng)),
                ("kv", "Valid until", _fmt_date(expiry, rng))]
        injection = variant == "injection"
        if injection:
            page.append(("small", rng.choice(INJECTIONS)))
        truth = UploadTruth(doc_type="security_certificate", owner_supplier_id=s.id if variant != "wrong_org" else None,
                            scenario_supplier_id=s.id,
                            fields={"organisation": org, "certification_body": body, "scope": scope,
                                    "expiry_date": expiry.isoformat(), "issue_date": issue.isoformat()},
                            injection=injection, format="auto", variant=variant,
                            expect="abstain" if variant == "wrong_org" else "match")
        return self._add_upload(s, s.tenant_id, "security_certificate", DocContent([page]), truth,
                                self._when(s, early=older))

    # bank letter -------------------------------------------------------------
    def _bank(self, s: Supplier, variant: str, older: bool = False) -> Upload:
        rng = self.rng
        letter = AS_OF - timedelta(days=rng.randint(5, 150))
        if older or variant == "old_letter":
            letter = AS_OF - timedelta(days=rng.randint(200, 400))
        iban = s.iban
        if variant == "changed_iban":                  # valid, but not the account on file
            iban = nm.iban(rng, rng.choice([b for b in nm.BANKS if b != s.bank]))
        if variant == "bad_iban":
            i = max(rng.randint(6, len(iban) - 1), 8)      # a digit of the account number, not the bank code
            iban = iban[:i] + str((int(iban[i]) + rng.randint(1, 8)) % 10) + iban[i + 1:]
        holder = s.legal_name if variant != "holder_mismatch" else \
            s.legal_name.split(" ")[0] + rng.choice([" Trading Ltd", " Payments Ltd", " Services UK Ltd"])
        page = [("title", rng.choice(["BANK ACCOUNT CONFIRMATION", "CONFIRMATION OF BANK DETAILS"])),
                ("kv", "Bank", s.bank), ("kv", "Date", _fmt_date(letter, rng)),
                ("para", "We confirm that the account below is held with us in the name shown."),
                ("kv", "Account holder", holder), ("kv", "IBAN", format_iban(iban)),
                ("kv", "BIC", s.bic),
                ("para", "This letter is provided at the request of our customer and without liability on the part of the bank.")]
        channel = rng.choices(["staff_verified", "supplier_portal", "email_ingest"], [0.6, 0.3, 0.1])[0]
        truth = UploadTruth(doc_type="bank_letter", owner_supplier_id=s.id, scenario_supplier_id=s.id,
                            fields={"account_holder": holder, "iban": iban, "letter_date": letter.isoformat(),
                                    "iban_valid": str(iban_valid(iban)), "iban_on_file": str(iban == s.iban)},
                            format="auto", variant=variant)
        return self._add_upload(s, s.tenant_id, "bank_letter", DocContent([page]), truth, self._when(s, early=older),
                                channel=channel)

    # tax registration --------------------------------------------------------
    def _tax(self, s: Supplier, variant: str) -> Upload:
        rng = self.rng
        name = s.legal_name if variant != "wrong_name" else s.legal_name.split(" ")[0] + " Former Trading Ltd"
        vat = s.vat_number if variant != "bad_vat" else s.vat_number[:6]
        lei = s.lei
        if lei and variant == "bad_lei":
            lei = lei[:-2] + f"{(int(lei[-2:]) + rng.randint(1, 50)) % 100:02d}"
        signed = AS_OF - timedelta(days=rng.randint(10, 300))
        page = [("title", rng.choice(["SUPPLIER TAX REGISTRATION FORM", "VAT REGISTRATION DETAILS"])),
                ("kv", "Legal name", name), ("kv", "Registered office", s.address),
                ("kv", "Company number", s.company_number), ("kv", "VAT number", vat)]
        if lei:
            page.append(("kv", "LEI", lei))
        page += [("kv", "Date signed", _fmt_date(signed, rng)),
                 ("para", "I confirm that the information above is correct and complete.")]
        truth = UploadTruth(doc_type="tax_registration", owner_supplier_id=s.id, scenario_supplier_id=s.id,
                            fields={"legal_name": name, "vat_number": vat, "lei": lei or "",
                                    "lei_valid": str(lei_valid(lei)) if lei else ""},
                            format="auto", variant=variant)
        return self._add_upload(s, s.tenant_id, "tax_registration", DocContent([page]), truth, self._when(s))

    # master agreement --------------------------------------------------------
    def _agreement(self, s: Supplier, tenant: Tenant, variant: str) -> Upload:
        rng = self.rng
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
        amount = _fmt_money(rng.choice([1_000_000, 2_000_000, 5_000_000]), rng)
        effective = AS_OF - timedelta(days=rng.randint(30, 500))
        signed = variant != "unsigned"
        p1 = [("title", "MASTER SERVICES AGREEMENT"),
              ("para", f"This Agreement is made between {tenant.name} (the Customer) and {s.legal_name} "
                       f"(company number {s.company_number}) (the Supplier)."),
              ("kv", "Supplier", s.legal_name), ("kv", "Effective date", _fmt_date(effective, rng)), ("gap",)]
        for i, text in enumerate(paras, start=1):
            p1.append(("para", f"{i}. " + text.format(amount=amount)))
        sup_person, cus_person = rng.sample(PEOPLE, 2)
        p1 += [("gap",), ("kv", "Signed for the Supplier", f"{sup_person}, Director, {_fmt_date(effective, rng)}"),
               ("kv", "Signed for the Customer",
                f"{cus_person}, Head of Procurement, {_fmt_date(effective, rng)}" if signed else "")]
        truth = UploadTruth(doc_type="master_agreement", owner_supplier_id=s.id, scenario_supplier_id=s.id,
                            fields={"supplier_party": s.legal_name}, injection=injection,
                            clauses=present, signed=signed, format="auto", variant=variant)
        return self._add_upload(s, s.tenant_id, "master_agreement", DocContent([p1]), truth, self._when(s),
                                channel=rng.choice(["staff_upload", "staff_upload", "email_ingest"]), single_page=False)

    def _unknown(self, tenant: Tenant) -> Upload:
        rng = self.rng
        if rng.random() < 0.5:
            page = [("title", "PRODUCT OVERVIEW"),
                    ("para", "Our team brings decades of experience to every project. We pride ourselves on service, "
                             "reliability and value, and we look forward to working with you."),
                    ("para", "Contact our sales team to arrange a demonstration.")]
        else:
            page = [("title", "MINUTES OF SUPPLIER REVIEW MEETING"),
                    ("kv", "Attendees", ", ".join(rng.sample(PEOPLE, 3))),
                    ("para", "The group reviewed delivery performance for the quarter and agreed two actions.")]
        truth = UploadTruth(doc_type="unknown", owner_supplier_id=None, scenario_supplier_id=None, expect="unknown",
                            format=rng.choice(["digital", "scan"]))
        when = datetime.combine(AS_OF - timedelta(days=rng.randint(5, 200)), time(10, 0))
        return self._add_upload(None, tenant.id, "unknown", DocContent([page]), truth, when, channel="email_ingest")

    VARIANTS = {
        "insurance": (["normal", "expired", "renewal_due", "low_limit", "wrong_insured", "decoy_page",
                       "conflicting_page", "old_issue", "injection"],
                      [0.44, 0.08, 0.08, 0.08, 0.06, 0.08, 0.06, 0.06, 0.06]),
        "security_certification": (["normal", "expired", "wrong_org", "unaccredited", "scope_mismatch", "injection"],
                                   [0.58, 0.1, 0.06, 0.1, 0.1, 0.06]),
        "bank_details": (["normal", "bad_iban", "holder_mismatch", "changed_iban", "old_letter"],
                         [0.64, 0.09, 0.09, 0.09, 0.09]),
        "tax_registration": (["normal", "wrong_name", "bad_vat", "bad_lei"], [0.7, 0.1, 0.1, 0.1]),
        "master_agreement": (["normal", "unsigned", "injection"], [0.82, 0.1, 0.08]),
    }

    def build_documents(self) -> None:
        rng = self.rng
        for tenant in self.tenants:
            for s in [x for x in self.suppliers if x.tenant_id == tenant.id and self._engagements_of(x)]:
                for rid in self._requirements_for(s):
                    if rid == "bank_details" and rng.random() < 0.06:
                        self.attestations.append(Attestation(
                            tenant_id=tenant.id, supplier_id=s.id, requirement_id=rid,
                            verified_by=rng.choice(PEOPLE), verified_on=AS_OF - timedelta(days=rng.randint(5, 60)),
                            note="Account details confirmed by call-back to the number on file."))
                        continue
                    if rng.random() < 0.08:
                        continue                                       # missing
                    variants, weights = self.VARIANTS[rid]
                    variant = rng.choices(variants, weights)[0]
                    if rid == "insurance":
                        if s.parent_id and rng.random() < 0.5:
                            variant = "group"
                        if variant != "group" and rng.random() < 0.1:
                            self._insurance(s, "normal", tenant, older=True)
                        self._insurance(s, variant, tenant)
                    elif rid == "security_certification":
                        if rng.random() < 0.1:
                            self._security(s, "normal", older=True)
                        self._security(s, variant)
                    elif rid == "bank_details":
                        self._bank(s, variant)
                    elif rid == "tax_registration":
                        self._tax(s, variant)
                    elif rid == "master_agreement":
                        self._agreement(s, tenant, variant)
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
            self.uploads.append(Upload(id=uid, tenant_id=u.tenant_id, business_unit_id=u.business_unit_id,
                                       filename=f"Copy of {u.filename}", file=rel, channel="email_ingest",
                                       uploaded_at=u.uploaded_at + timedelta(days=rng.randint(1, 20)),
                                       claimed_supplier_id=None, truth=truth))
        # misfiled: uploaded to a business unit where the supplier has no engagement
        for u in rng.sample([u for u in originals if u.truth.expect == "match" and u.truth.duplicate_of is None],
                            k=max(1, len(originals) // 25)):
            owner = u.truth.owner_supplier_id
            engaged = {e.business_unit_id for e in self.engagements if e.supplier_id == owner}
            others = [b.id for b in self.bus if b.tenant_id == u.tenant_id and b.id not in engaged]
            if not others or u.claimed_supplier_id is not None:
                continue
            u.business_unit_id = rng.choice(others)
            u.truth.variant += "+misfiled"
            # identifiers on the page let a careful engine place it anyway; a name alone does not
            if u.truth.doc_type not in ("bank_letter", "tax_registration", "master_agreement"):
                u.truth.expect = "abstain"
        # a document sent to the wrong tenant: it names a supplier that tenant does not have
        if len(self.tenants) > 1:
            u = rng.choice([x for x in originals if x.tenant_id == 1 and x.truth.doc_type == "tax_registration"])
            uid = self._next("upload")
            rel = f"files/{uid:05d}{Path(u.file).suffix}"
            (self.out / rel).write_bytes((self.out / u.file).read_bytes())
            truth = u.truth.model_copy(deep=True)
            truth.owner_supplier_id, truth.scenario_supplier_id, truth.expect = None, None, "abstain"
            truth.variant = "wrong_tenant"
            t2 = self.tenants[1]
            self.uploads.append(Upload(id=uid, tenant_id=t2.id,
                                       business_unit_id=next(b.id for b in self.bus if b.tenant_id == t2.id),
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
    world = World(seed=seed, size=size, as_of=AS_OF, pack=pack.digest, tenants=b.tenants, business_units=b.bus,
                  suppliers=b.suppliers, engagements=b.engagements, attestations=b.attestations, uploads=b.uploads)
    (out / "world.json").write_text(world.model_dump_json(indent=1), encoding="utf-8")
    return world


def load_world(path: str | Path) -> World:
    return World.model_validate_json((Path(path) / "world.json").read_text(encoding="utf-8"))
