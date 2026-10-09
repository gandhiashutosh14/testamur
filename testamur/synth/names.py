"""Fictional names, addresses and identifiers. Every name is assembled from generic words; none refers to a real
organisation."""
from __future__ import annotations

import random
import string

from ..ids import iban_with_check, lei_with_check

FIRST = ["Brightwater", "Northgate", "Harbourline", "Kestrel", "Ashford", "Corrin", "Lumen", "Vantage", "Meridale",
         "Stonebridge", "Fallowfield", "Greyhaven", "Oakhurst", "Riverton", "Silvermere", "Thornbury", "Westbrook",
         "Ambergate", "Blackmoor", "Copperfield", "Eastholm", "Foxley", "Glenmore", "Hollins", "Ivybridge", "Juniper",
         "Kingsmere", "Larchmont", "Marlowe", "Netherby", "Oxleigh", "Pennant", "Quarry Hill", "Redcliffe", "Saltmarsh",
         "Tidewell", "Upton Vale", "Verity", "Wychwood", "Yarrow"]
INDUSTRY = {
    "cloud_hosting": ["Hosting", "Cloud Systems", "Data Centres"],
    "payroll_services": ["Payroll Services", "Payroll Bureau", "People Payments"],
    "logistics": ["Logistics", "Freight", "Haulage"],
    "facilities": ["Cleaning", "Facilities", "Property Services"],
    "software_development": ["Software", "Digital", "Applications"],
}
SUFFIX = ["Ltd", "Limited", "plc", "GmbH", "BV", "Ltd", "Ltd"]
STREETS = ["Quay Street", "Market Row", "Mill Lane", "Station Road", "Harbour Way", "Chapel Walk", "Foundry Yard",
           "Canal Side", "Abbey Close", "Weaver Street", "Granary Court", "Bridge End"]
TOWNS = ["Leith", "Halbury", "Kirkwall", "Dunmore", "Ashby Fold", "Penrith Vale", "Carrow", "Elmswick", "Tarnby",
         "Wexley"]
BANKS = ["Calder Commercial Bank", "Northern Mercantile Bank", "Harbour Trust Bank", "Union Exchange Bank"]
BANK_CODES = {"Calder Commercial Bank": "CALD", "Northern Mercantile Bank": "NMBK", "Harbour Trust Bank": "HTBK",
              "Union Exchange Bank": "UXBK"}
INSURERS = ["Harbourline Mutual Insurance plc", "Granite Assurance Company", "Ashgrove Underwriting Ltd",
            "Polaris Indemnity Group"]
UNACCREDITED = ["Global Certificates Online", "QuickCert International", "Assurance Hub Registrar"]


def legal_name(rng: random.Random, category: str, used: set[str]) -> str:
    for _ in range(500):
        name = f"{rng.choice(FIRST)} {rng.choice(INDUSTRY[category])} {rng.choice(SUFFIX)}"
        key = name.rsplit(" ", 1)[0]
        if key not in used:
            used.add(key)
            return name
    raise RuntimeError("name space exhausted")


def near_duplicate(name: str, rng: random.Random) -> str:
    """A different company whose name a careless matcher would confuse with this one."""
    base, suffix = name.rsplit(" ", 1)
    variants = [f"{base} Services {suffix}", f"{base} Group {suffix}", f"{base} ({rng.choice(TOWNS)}) {suffix}",
                f"{base.split(' ')[0]} {base.split(' ', 1)[1] if ' ' in base else base} UK {suffix}"]
    return rng.choice(variants)


def address(rng: random.Random) -> str:
    code = f"{rng.choice(string.ascii_uppercase)}{rng.choice(string.ascii_uppercase)}{rng.randint(1, 19)} " \
           f"{rng.randint(1, 9)}{rng.choice(string.ascii_uppercase)}{rng.choice(string.ascii_uppercase)}"
    return f"{rng.randint(1, 240)} {rng.choice(STREETS)}, {rng.choice(TOWNS)}, {code}"


def company_number(rng: random.Random) -> str:
    return f"{rng.randint(10_000_000, 99_999_999)}"


def vat_number(rng: random.Random) -> str:
    return f"GB{rng.randint(100_000_000, 999_999_999)}"


def lei(rng: random.Random) -> str:
    prefix = "".join(rng.choice(string.digits + string.ascii_uppercase) for _ in range(4)) + "00" + \
             "".join(rng.choice(string.digits + string.ascii_uppercase) for _ in range(12))
    return lei_with_check(prefix)


def iban(rng: random.Random, bank: str) -> str:
    bban = BANK_CODES[bank] + "".join(rng.choice(string.digits) for _ in range(14))
    return iban_with_check("GB", bban)


def bic(bank: str) -> str:
    return BANK_CODES[bank] + "GB2L"
