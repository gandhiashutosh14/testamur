"""TESTAMUR: evidence assurance for supplier compliance.

Documents arrive messy; the engine reads them, works out whose they are, checks them at the cheapest tier that can
decide, and records a verdict with its evidence. It never says PASS without evidence, and it abstains rather than
guesses.
"""

__version__ = "0.2.0"
