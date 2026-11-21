"""Contact data that cannot belong to a real person.

Every email address, phone number and IP address the generator writes comes from a range reserved for examples
or fiction, so a generated record can never point at a real person:

* Email domains end in `.example`, a top-level domain reserved for documentation (RFC 2606, RFC 6761).
* Phone numbers use the six blocks ARCEP reserves for audiovisual works ("Numéros pour œuvres
  audiovisuelles"): roots 01 99 00, 02 61 91, 03 53 01, 04 65 71, 05 36 49 and 06 39 98, each
  followed by four free digits.
  Source: ARCEP, Plan national de numérotation, version of 1 January 2026, p. 55/67 (annex to decision
  n° 2018-0881 as amended): "Les numéros pour œuvres audiovisuelles peuvent être utilisés comme numéros de
  téléphone dans des fictions qui en auraient besoin."
* IP addresses come from the IPv4 documentation blocks (RFC 5737).

The validators below are also the basis for the public demo's check that visitor text holds no real
contact data.
"""

from __future__ import annotations

import ipaddress
import re

SHOP_NAME = "Ferlane"
SHOP_DOMAIN = "ferlane.example"
SHOP_PRIVACY_EMAIL = f"dpo@{SHOP_DOMAIN}"

RESERVED_TLD = ".example"
MAIL_DOMAINS = ("courriel.example", "boitemail.example", "webpost.example", "lettre.example")

PHONE_ROOTS = ("019900", "026191", "035301", "046571", "053649", "063998")
"""ARCEP fiction blocks, national format without spaces (0ZABPQ); four digits (MCDU) follow."""

IP_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))

_EMAIL = re.compile(r"^[a-z0-9._+-]+@[a-z0-9.-]+$")
_SEPARATORS = re.compile(r"[\s.\-]")


def is_fictional_email(address: str) -> bool:
    address = address.strip().lower()
    if not _EMAIL.match(address):
        return False
    domain = address.rsplit("@", 1)[1]
    return domain.endswith(RESERVED_TLD)


def normalise_phone(number: str) -> str:
    digits = _SEPARATORS.sub("", number.strip())
    if digits.startswith("+33"):
        digits = "0" + digits[3:]
    return digits


def is_fictional_phone(number: str) -> bool:
    digits = normalise_phone(number)
    return len(digits) == 10 and digits.isdigit() and digits[:6] in PHONE_ROOTS


def format_phone(root: str, line: int) -> str:
    """`root` from PHONE_ROOTS, `line` 0-9999 -> '06 39 98 12 34'."""
    if root not in PHONE_ROOTS or not 0 <= line <= 9999:
        raise ValueError("phone numbers must stay inside the ARCEP fiction blocks")
    digits = f"{root}{line:04d}"
    return " ".join(digits[i : i + 2] for i in range(0, 10, 2))


def is_fictional_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in IP_NETWORKS)
