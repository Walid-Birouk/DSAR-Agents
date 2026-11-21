"""The fictional company's records, the request cases, and the ground truth the generator knows.

Records go into the four company systems (one SQLite database each). Cases are what the desk receives.
Truth is what a correct answer looks like; it never goes into a system database, so no agent or tool can
read it.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field

from article15.schemas import Channel, IdentityOutcome, Language, RequestType, Strict, System

# --- company records -------------------------------------------------------------------------------------


class Customer(Strict):
    customer_id: str
    first_name: str
    last_name: str
    email: str
    phone: str
    language: Language
    street: str
    postcode: str
    city: str
    created_on: date


class Consent(Strict):
    consent_id: str
    customer_id: str
    purpose: str
    granted: bool
    recorded_at: datetime


class PriorRequest(Strict):
    """An earlier data-protection request, kept in the CRM."""

    request_id: str
    customer_id: str
    received_on: date
    request_type: RequestType
    status: str


class Order(Strict):
    order_id: str
    customer_id: str | None  # None for a guest checkout
    guest_email: str | None
    placed_at: datetime
    status: str
    ship_name: str
    ship_street: str
    ship_postcode: str
    ship_city: str
    total_cents: int


class OrderLine(Strict):
    line_id: str
    order_id: str
    sku: str
    product_name: str
    quantity: int
    unit_price_cents: int


class Author(StrEnum):
    CUSTOMER = "customer"
    STAFF = "staff"


class Ticket(Strict):
    ticket_id: str
    customer_id: str | None  # set when the requester email was registered when the ticket was opened
    requester_email: str
    subject: str
    opened_at: datetime
    status: str


class TicketMessage(Strict):
    message_id: str
    ticket_id: str
    author: Author
    staff_name: str | None
    sent_at: datetime
    body: str


class WebEvent(Strict):
    event_id: str
    ts: datetime
    cookie_id: str
    account_id: str | None  # the customer ID when logged in
    ip: str
    path: str
    user_agent: str


# --- ground truth ----------------------------------------------------------------------------------------


class RecordKey(Strict):
    system: System
    table: str
    record_id: str

    def sort_key(self) -> tuple[str, str, str]:
        return (self.system.value, self.table, self.record_id)


class SpanCategory(StrEnum):
    THIRD_PARTY = "third_party"  # other customers and private individuals: redacted from policy v1
    STAFF = "staff"  # staff names: redacted from policy v2 (experiment E3)


class TruthSpan(Strict):
    """A piece of free text about someone other than the data subject."""

    record: RecordKey
    field: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    category: SpanCategory
    text: str
    note: str


class SpecialMention(Strict):
    """Special-category data (GDPR Art. 9(1)) about the subject: kept in the pack, flagged for review."""

    record: RecordKey
    field: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    category: str  # "health"
    text: str


class Person(Strict):
    """Every human in the fictional world. Customers share their customer ID as person ID."""

    person_id: str
    name: str
    mailboxes: list[str]  # addresses this person can read (used for proof of control)
    customer_id: str | None


class RecordOwner(Strict):
    """Who a record is really about. The oracle for the scope tests; never stored in a system."""

    record: RecordKey
    person_id: str


class CaseKind(StrEnum):
    STANDARD = "standard"
    IDENTITY_OTHER_EMAIL = "identity_other_email"
    IDENTITY_IMPOSTOR = "identity_impostor"
    IDENTITY_REPRESENTATIVE = "identity_representative_no_mandate"
    IDENTITY_NO_ACCOUNT = "identity_no_account"
    EDGE_REPEAT = "edge_repeat"
    EDGE_OTHER_RIGHTS = "edge_other_rights"
    EDGE_EXTENSION = "edge_extension"
    ADV_INJECTION_REQUEST = "adversarial_injection_request"
    ADV_INJECTION_RECORD = "adversarial_injection_record"
    ADV_MALFORMED = "adversarial_malformed"
    ADV_OVERSIZED = "adversarial_oversized"


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"


class Resolution(StrEnum):
    RELEASE_PACK = "release_pack"  # verified subject, pack goes to review and release
    ASK_REQUESTER = "ask_requester"  # cannot go further without something only the requester can give
    REFUSE = "refuse"  # identity not established; explain why (Art. 12(4))
    NO_DATA = "no_data"  # nothing is held under the details given; say so
    ROUTE_TO_HUMAN = "route_to_human"  # edge case: a person decides (R-EXCESSIVE, other rights, extension)


class RequestCase(Strict):
    """What the desk receives. Nothing here says what the right answer is."""

    case_id: str
    channel: Channel
    sender: str  # the address the request came from (email) or was entered with (web form)
    received_on: date
    subject_line: str
    text: str


class RequesterProfile(Strict):
    """Who really sent the request and what they can answer. Drives the simulated requester in the evals."""

    person_id: str
    mailboxes: list[str]
    knows_order_reference: str | None
    knows_postcode: str | None
    has_mandate: bool = False


class CaseTruth(Strict):
    case_id: str
    kind: CaseKind
    split: Split
    golden: bool
    language: Language
    subject_id: str | None  # the customer the request is really about, if any
    requester: RequesterProfile
    request_types: list[RequestType]
    expected_identity: IdentityOutcome  # the identity decision on the request as received
    expected_final_identity: IdentityOutcome  # after the simulated requester has answered any follow-up
    expected_resolution: Resolution
    edge_reason: str | None
    due: date
    records: list[RecordKey]  # everything the pack must contain (empty when nothing may be released)
    links_needing_proof: list[str]  # the subject's addresses found only in free text
    lookalikes: list[RecordKey]  # records that resemble the subject's but belong to someone else
    redactions: list[TruthSpan]
    special_category: list[SpecialMention]
    embedded_instructions: bool
    difficulties: list[str]


class World(Strict):
    seed: int
    generator_version: str
    customers: list[Customer]
    consents: list[Consent]
    prior_requests: list[PriorRequest]
    orders: list[Order]
    order_lines: list[OrderLine]
    tickets: list[Ticket]
    messages: list[TicketMessage]
    events: list[WebEvent]
    people: list[Person]
    owners: list[RecordOwner]
    cases: list[RequestCase]
    truths: list[CaseTruth]
