"""Who a case may read, decided in code.

* `ScopeAuthority` (control plane) issues grants. An identity-stage grant lets the identity agent check
  claims and write to the requester. A subject grant, which unlocks the verified person's records, is
  issued only if code confirms the evidence itself (guard `scope.identity_evidence`, rule R-IDENTITY):
  the request came from the person's registered email, or the requester gave an order number of theirs
  together with that order's postcode.
* Widening a subject grant to an address found only in free text needs proof of control: a one-time code is
  sent to that address and must come back (guard `mcp.subject_scope`, rule R-NO-FOREIGN-DATA).
* `ScopeResolver` (MCP servers, read-only) turns a case token into the scope the servers filter by.

Every refusal is a `ScopeRefused` with a short reason the model can act on.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from article15.company.store import DataPaths, read_only
from article15.notifier.store import Kind, NotifierStore
from article15.schemas import System
from article15.scope import tokens
from article15.scope.store import (
    ChallengeRow,
    ChallengeStatus,
    GrantRow,
    IdentifierKind,
    ScopeStore,
    Stage,
)
from article15.settings import DEFAULT_GRANT_TTL

GUARD_SUBJECT_SCOPE = "mcp.subject_scope"
GUARD_IDENTITY_EVIDENCE = "scope.identity_evidence"
SCOPE_GUARDS: dict[str, str] = {
    GUARD_SUBJECT_SCOPE: "R-NO-FOREIGN-DATA",
    GUARD_IDENTITY_EVIDENCE: "R-IDENTITY",
}
"""Guards outside the case state machine, for the policy linter: guard name -> rule ID."""

LINK_CODE_DIGITS = 6
LINK_CODE_TTL = timedelta(days=7)
LINK_MAX_ATTEMPTS = 3
MAX_LINKS_PER_CASE = 3

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ScopeRefused(Exception):
    """A request outside what the case may do. The message is shown to the model as the tool result."""

    def __init__(self, reason: str, guard: str = GUARD_SUBJECT_SCOPE) -> None:
        super().__init__(reason)
        self.guard = guard
        self.rule_id = SCOPE_GUARDS[guard]


@dataclass(frozen=True)
class Scope:
    grant_id: str
    case_id: str
    stage: Stage
    subject_id: str | None
    sender: str
    expires_at: datetime
    emails: frozenset[str] = field(default_factory=frozenset)
    cookies: frozenset[str] = field(default_factory=frozenset)

    def require_subject(self) -> str:
        if self.stage is not Stage.SUBJECT or self.subject_id is None:
            raise ScopeRefused(
                "the requester's identity is not verified yet, so no records can be read for this case"
            )
        return self.subject_id


def _norm_email(address: str) -> str:
    return address.strip().lower()


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _spaced(text: str) -> str:
    """Case-folded, with every run of whitespace turned into one space (so values stay separate words)."""
    return re.sub(r"\s+", " ", text).casefold().strip()


class ScopeResolver:
    """Read-only: used by the MCP servers on every tool call."""

    def __init__(self, store: ScopeStore, secret: bytes, clock: Clock = utc_now) -> None:
        self.store = store
        self.secret = secret
        self.clock = clock

    def resolve(self, token: str) -> Scope:
        try:
            grant_id = tokens.grant_id_of(self.secret, token)
        except tokens.InvalidToken as exc:
            raise ScopeRefused(f"invalid case token ({exc})") from None
        loaded = self.store.load(grant_id) if self.store.path.exists() else None
        if loaded is None:
            raise ScopeRefused("unknown case token")
        grant, identifiers, links = loaded
        if grant.revoked:
            raise ScopeRefused("this case token has been revoked")
        if self.clock() >= grant.expires_at:
            raise ScopeRefused("this case token has expired; the case must be resumed to get a new one")
        emails: set[str] = set()
        cookies: set[str] = set()
        for kind, value in identifiers:
            (emails if kind is IdentifierKind.EMAIL else cookies).add(value)
        if grant.stage is Stage.SUBJECT and grant.subject_id:
            emails.update(links)
        return Scope(
            grant_id=grant.grant_id,
            case_id=grant.case_id,
            stage=grant.stage,
            subject_id=grant.subject_id,
            sender=grant.sender,
            expires_at=grant.expires_at,
            emails=frozenset(emails),
            cookies=frozenset(cookies),
        )


LinkCode = Callable[[str, str], str]
"""(case ID, address) -> the one-time code sent to the address."""


def random_link_code(case_id: str, address: str) -> str:
    """A real deployment: a fresh random code for every challenge."""
    return f"{secrets.randbelow(10**LINK_CODE_DIGITS):0{LINK_CODE_DIGITS}d}"


def keyed_link_code(key: bytes) -> LinkCode:
    """The simulated desk: the code is an HMAC of the case and the address under `key`. Recorded runs use a
    fixed key, so the simulated requester's reply (which quotes the code) is the same on every run and the
    recording replays; the public web app uses a fresh secret key per run, so a visitor cannot compute one."""

    def code(case_id: str, address: str) -> str:
        digest = hmac.new(key, f"link-code|{case_id}|{address}".encode(), hashlib.sha256).digest()
        return f"{int.from_bytes(digest[:8], 'big') % 10**LINK_CODE_DIGITS:0{LINK_CODE_DIGITS}d}"

    return code


class ScopeAuthority:
    """Control plane: the only writer of the scope store."""

    def __init__(
        self,
        data: DataPaths,
        store: ScopeStore,
        notifier: NotifierStore,
        secret: bytes,
        clock: Clock = utc_now,
        grant_ttl: timedelta = DEFAULT_GRANT_TTL,
        link_code: LinkCode | None = None,
    ) -> None:
        if store.read_only:
            raise ValueError("the authority needs a writable scope store")
        self.data = data
        self.store = store
        self.notifier = notifier
        self.secret = secret
        self.clock = clock
        self.grant_ttl = grant_ttl
        self.link_code = link_code or random_link_code
        self.resolver = ScopeResolver(store, secret, clock)

    # --- grants ----------------------------------------------------------------------------------------

    def _issue(
        self,
        case_id: str,
        stage: Stage,
        subject_id: str | None,
        sender: str,
        evidence: str,
        identifiers: list[tuple[IdentifierKind, str, str]],
    ) -> str:
        now = self.clock()
        grant_id = tokens.new_grant_id()
        self.store.insert_grant(
            GrantRow(
                grant_id=grant_id,
                case_id=case_id,
                stage=stage,
                subject_id=subject_id,
                sender=_norm_email(sender),
                issued_at=now,
                expires_at=now + self.grant_ttl,
                revoked=False,
                evidence=evidence,
            ),
            identifiers,
        )
        return tokens.issue(self.secret, grant_id)

    def open_case(self, case_id: str, sender: str) -> str:
        """Identity-stage grant for a new case. `sender` is the address the request came from."""
        return self._issue(case_id, Stage.IDENTITY, None, sender, "request received", [])

    def grant_subject(
        self,
        identity_token: str,
        subject_id: str,
        request_text: str,
        order_reference: str | None = None,
        postcode: str | None = None,
    ) -> str:
        """Subject grant, issued only if code confirms the identity evidence (R-IDENTITY)."""
        scope = self.resolver.resolve(identity_token)
        if scope.stage is not Stage.IDENTITY:
            raise ScopeRefused("a subject grant is issued from the case's identity-stage token")
        refuse = GUARD_IDENTITY_EVIDENCE
        with read_only(self.data.db(System.CRM)) as crm:
            customer = crm.execute(
                "SELECT customer_id, email FROM customers WHERE customer_id = ?", (subject_id,)
            ).fetchone()
        if customer is None:
            raise ScopeRefused(f"the evidence does not identify customer {subject_id}", refuse)
        if _norm_email(customer["email"]) == scope.sender:
            evidence = "registered_email"
        else:
            evidence = self._order_and_postcode(
                scope.case_id, subject_id, request_text, order_reference, postcode
            )
        return self._issue(
            scope.case_id,
            Stage.SUBJECT,
            subject_id,
            scope.sender,
            evidence,
            [
                (IdentifierKind.EMAIL, _norm_email(customer["email"]), "crm.registered_email"),
                *(
                    (IdentifierKind.COOKIE, c, "weblogs.authenticated_cookie")
                    for c in self._own_cookies(subject_id)
                ),
            ],
        )

    def _order_and_postcode(
        self,
        case_id: str,
        subject_id: str,
        request_text: str,
        order_reference: str | None,
        postcode: str | None,
    ) -> str:
        refuse = GUARD_IDENTITY_EVIDENCE
        if not order_reference or not postcode:
            raise ScopeRefused(
                "the request did not come from the registered email; verification needs an order number of "
                "the customer and that order's postcode",
                refuse,
            )
        # The values must come from the requester, not from the agent: they must appear in what they wrote.
        written = [
            _spaced(t) for t in (request_text, *(note.body for note in self.notifier.replies(case_id)))
        ]
        ref, code = _spaced(order_reference), _squash(postcode)
        if not any(ref in t for t in written) or not any(
            re.search(rf"(?<!\d){re.escape(code)}(?!\d)", t) for t in written
        ):
            raise ScopeRefused(
                "the order number and postcode must be ones the requester actually wrote", refuse
            )
        with read_only(self.data.db(System.ORDERS)) as orders:
            row = orders.execute(
                "SELECT ship_postcode FROM orders WHERE order_id = ? AND customer_id = ?",
                (order_reference.strip().upper(), subject_id),
            ).fetchone()
        if row is None or _squash(row["ship_postcode"]) != code:
            raise ScopeRefused("the order number and postcode do not match this customer", refuse)
        return f"order_and_postcode:{order_reference.strip().upper()}"

    def _own_cookies(self, subject_id: str) -> list[str]:
        """Cookies this customer logged in with, and nobody else ever did (a shared browser is not linked)."""
        with read_only(self.data.db(System.WEBLOGS)) as weblogs:
            rows = weblogs.execute(
                "SELECT DISTINCT cookie_id FROM events WHERE account_id = ? EXCEPT SELECT cookie_id FROM "
                "events WHERE account_id IS NOT NULL AND account_id != ? ORDER BY cookie_id",
                (subject_id, subject_id),
            ).fetchall()
        return [r["cookie_id"] for r in rows]

    def revoke_case(self, case_id: str) -> None:
        self.store.revoke_case(case_id)

    # --- proof of control -----------------------------------------------------------------------------

    def _code_hash(self, challenge_id: str, code: str) -> str:
        return hmac.new(self.secret, f"link|{challenge_id}|{code}".encode(), hashlib.sha256).hexdigest()

    def start_link(self, token: str, email: str, evidence_ticket_id: str) -> None:
        """Send a one-time code to an address found in the subject's own helpdesk message."""
        scope = self.resolver.resolve(token)
        subject_id = scope.require_subject()
        address = _norm_email(email)
        if not _EMAIL.match(address):
            raise ScopeRefused(f"'{email}' is not an email address")
        if address in scope.emails:
            raise ScopeRefused(f"{address} is already part of this case's scope")
        if len(self.store.linked_emails(scope.case_id, subject_id)) >= MAX_LINKS_PER_CASE:
            raise ScopeRefused(f"at most {MAX_LINKS_PER_CASE} extra addresses can be linked to one case")
        self._check_link_evidence(scope, address, evidence_ticket_id)
        with read_only(self.data.db(System.CRM)) as crm:
            other = crm.execute(
                "SELECT 1 FROM customers WHERE lower(email) = ? AND customer_id != ?", (address, subject_id)
            ).fetchone()
        if other is not None:
            raise ScopeRefused(f"{address} is registered to another customer account; a person must decide")
        now = self.clock()
        challenge_id = secrets.token_hex(8)
        code = self.link_code(scope.case_id, address)
        self.store.insert_challenge(
            ChallengeRow(
                challenge_id=challenge_id,
                case_id=scope.case_id,
                subject_id=subject_id,
                email=address,
                code_hash=self._code_hash(challenge_id, code),
                issued_at=now,
                expires_at=now + LINK_CODE_TTL,
                attempts=0,
                status=ChallengeStatus.PENDING,
                evidence=f"helpdesk:{evidence_ticket_id}",
            )
        )
        self.notifier.send(
            scope.case_id,
            to=address,
            subject="Ferlane : code de confirmation / confirmation code",
            body=(
                f"Votre code de confirmation est {code}. Il est valable {LINK_CODE_TTL.days} jours.\nYour "
                f"confirmation code is {code}. It is valid for {LINK_CODE_TTL.days} days.\n"
                "Renvoyez-le en réponse à votre demande d'accès pour que nous incluions les données liées à "
                "cette adresse. / Send it back in reply to your access request so we can include the data "
                "linked to this address."
            ),
            at=now,
            kind=Kind.LINK_CODE,
        )

    def _check_link_evidence(self, scope: Scope, address: str, ticket_id: str) -> None:
        with read_only(self.data.db(System.HELPDESK)) as helpdesk:
            ticket = helpdesk.execute(
                "SELECT customer_id, requester_email FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            in_scope = ticket is not None and (
                ticket["customer_id"] == scope.subject_id
                or _norm_email(ticket["requester_email"]) in scope.emails
            )
            if not in_scope:
                raise ScopeRefused(f"ticket {ticket_id} is not part of this case's scope")
            bodies = helpdesk.execute(
                "SELECT body FROM messages WHERE ticket_id = ? AND author = 'customer'", (ticket_id,)
            ).fetchall()
        if not any(address in b["body"].lower() for b in bodies):
            raise ScopeRefused(
                f"{address} does not appear in the customer's own messages in ticket {ticket_id}"
            )

    def complete_link(self, token: str, email: str, code: str) -> None:
        """Add the address to the case's scope if the code the requester sent back is right."""
        scope = self.resolver.resolve(token)
        subject_id = scope.require_subject()
        address = _norm_email(email)
        challenge = self.store.latest_challenge(scope.case_id, address)
        if challenge is None or challenge.status is not ChallengeStatus.PENDING:
            raise ScopeRefused(f"no confirmation code is pending for {address}")
        now = self.clock()
        if now >= challenge.expires_at:
            self.store.update_challenge(challenge.challenge_id, challenge.attempts, ChallengeStatus.EXPIRED)
            raise ScopeRefused(f"the confirmation code for {address} has expired; a new one must be sent")
        if hmac.compare_digest(self._code_hash(challenge.challenge_id, code.strip()), challenge.code_hash):
            self.store.update_challenge(challenge.challenge_id, challenge.attempts, ChallengeStatus.CONFIRMED)
            self.store.add_link(scope.case_id, subject_id, address, now, challenge.evidence)
            return
        attempts = challenge.attempts + 1
        if attempts >= LINK_MAX_ATTEMPTS:
            self.store.update_challenge(challenge.challenge_id, attempts, ChallengeStatus.BURNED)
            raise ScopeRefused(
                f"wrong code; the code for {address} is now void after {LINK_MAX_ATTEMPTS} attempts"
            )
        self.store.update_challenge(challenge.challenge_id, attempts, ChallengeStatus.PENDING)
        raise ScopeRefused(f"wrong code for {address}; {LINK_MAX_ATTEMPTS - attempts} attempt(s) left")
