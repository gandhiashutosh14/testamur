"""Fictional names, addresses and identifiers. Every name is assembled from generic words; none refers to a real
organisation, and the banks, registrar offices and companies are invented. Identifiers are well-formed (a GSTIN
carries a correct check character, an LEI correct check digits) but belong to nobody."""
from __future__ import annotations

import random
import string

from ..ids import gstin_with_check, lei_with_check

FIRST = ["Aadhira", "Akshara", "Anvaya", "Aranya", "Bhavya", "Charvi", "Dhanvi", "Ekansh", "Gaurika", "Hiranya",
         "Ishita", "Jivika", "Kaustubh", "Lavanya", "Madhvi", "Mrinmay", "Navya", "Nirvaan", "Ojasvi", "Parnika",
         "Prakhar", "Rudraksh", "Sanvika", "Shubhra", "Tanmay", "Tejasvi", "Udbhav", "Vaidehi", "Yashasvi", "Aditri",
         "Devika", "Kalindi", "Vasudha", "Ritvik", "Saanvi", "Ishanvi", "Avyukt", "Mahira", "Shaurya", "Trisha"]
INDUSTRY = {
    "working_capital": ["Fincorp", "Credit", "Capital"],
    "term_loan": ["Infra Credit", "Industrial Finance", "Project Finance"],
    "equipment_finance": ["Equipment Finance", "Leasing", "Machinery Finance"],
    "vehicle_finance": ["Motor Finance", "Auto Credit", "Fleet Finance"],
    "inter_corporate_deposit": ["Investments", "Treasury Services", "Commercial Investments"],
}
SUFFIX = ["Ltd", "Limited", "Pvt Ltd", "Private Limited", "Ltd", "Pvt Ltd"]
LEGAL_FORMS = sorted(set(SUFFIX), key=len, reverse=True)
TRUSTEE_ROOTS = ["Dhruvatara", "Sthirta", "Aashraya", "Nishtha", "Pratibhuti", "Samarthan"]
BUILDINGS = ["Shreeji Chambers", "Sai Arcade", "Commerce House", "Trade Centre", "Corporate Point", "Business Bay",
             "Mercantile House", "Sterling Chambers"]
STREETS = ["MG Road", "Station Road", "Ring Road", "Link Road", "Nehru Marg", "Tilak Road", "LBS Marg",
           "Residency Road", "College Road", "Market Yard Road"]
# city, state code for a CIN, state code for a GSTIN, first three digits of its PIN codes
CITIES = [("Pune", "MH", 27, "411"), ("Nashik", "MH", 27, "422"), ("Nagpur", "MH", 27, "440"), ("Indore", "MP", 23, "452"),
          ("Surat", "GJ", 24, "395"), ("Vadodara", "GJ", 24, "390"), ("Coimbatore", "TN", 33, "641"),
          ("Jaipur", "RJ", 8, "302"), ("Lucknow", "UP", 9, "226"), ("Kochi", "KL", 32, "682")]
UNRECOGNISED = ["Charge Filing Services Pvt Ltd", "Online Charge Registry", "Corporate Records Bureau"]
PEOPLE = ["Ananya Kulkarni", "Rohan Deshpande", "Meera Iyer", "Arjun Mehta", "Kavita Nair", "Sandeep Rao",
          "Priya Menon", "Vikram Joshi", "Neha Bhatt", "Aditya Sen", "Farah Siddiqui", "Gurpreet Sandhu",
          "Lakshmi Raghavan", "Manoj Pillai"]
TITLES = ["Assistant General Manager", "Chief Manager", "Vice President, Legal", "Company Secretary",
          "Head, Stressed Assets", "Authorised Officer"]
# NIC 2008 codes for lending and other financial service activities
NIC = ["64920", "64990", "64910", "66190"]


def split_form(name: str) -> tuple[str, str]:
    """('Anvaya Fincorp', 'Pvt Ltd') from 'Anvaya Fincorp Pvt Ltd'."""
    for form in LEGAL_FORMS:
        if name.endswith(" " + form):
            return name[: -len(form) - 1], form
    return name, ""


def legal_name(rng: random.Random, category: str, used: set[str]) -> str:
    for _ in range(500):
        name = f"{rng.choice(FIRST)} {rng.choice(INDUSTRY[category])} {rng.choice(SUFFIX)}"
        key = split_form(name)[0]
        if key not in used:
            used.add(key)
            return name
    raise RuntimeError("name space exhausted")


def near_duplicate(name: str, rng: random.Random) -> str:
    """A different company whose name a careless matcher would confuse with this one."""
    base, form = split_form(name)
    city = rng.choice(CITIES)[0]
    return rng.choice([f"{base} Services {form}", f"Shree {base} {form}", f"{base} ({city}) {form}",
                       f"{base} India {form}"])


def place(rng: random.Random) -> tuple[str, str, int, str]:
    return rng.choice(CITIES)


def address(rng: random.Random, city: tuple[str, str, int, str]) -> str:
    return (f"{rng.randint(1, 240)}, {rng.choice(BUILDINGS)}, {rng.choice(STREETS)}, {city[0]} "
            f"{city[3]}{rng.randint(1, 99):03d}")


def pan(rng: random.Random, name: str, holder: str = "C") -> str:
    """Three letters, the kind of holder ('C': a company), the first letter of the name, four digits, a letter."""
    first = next((ch for ch in name.upper() if ch.isalpha()), "X")
    return ("".join(rng.choice(string.ascii_uppercase) for _ in range(3)) + holder + first +
            f"{rng.randint(1, 9999):04d}" + rng.choice(string.ascii_uppercase))


def gstin(state_code: int, pan_value: str) -> str:
    return gstin_with_check(state_code, pan_value)


def cin(rng: random.Random, name: str, state: str, listed: bool = False) -> str:
    kind = "PTC" if "Pvt" in name or "Private" in name else "PLC"
    return f"{'L' if listed else 'U'}{rng.choice(NIC)}{state}{rng.randint(1995, 2019)}{kind}{rng.randint(1, 999999):06d}"


def lei(rng: random.Random) -> str:
    prefix = "".join(rng.choice(string.digits + string.ascii_uppercase) for _ in range(4)) + "00" + \
             "".join(rng.choice(string.digits + string.ascii_uppercase) for _ in range(12))
    return lei_with_check(prefix)


def account_number(rng: random.Random) -> str:
    return "".join(rng.choice(string.digits) for _ in range(rng.choice([12, 14, 15])))


def ifsc(rng: random.Random, bank_code: str) -> str:
    return f"{bank_code}0{rng.randint(1, 999999):06d}"
