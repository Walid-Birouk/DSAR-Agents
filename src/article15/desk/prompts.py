"""The single agent's prompt (experiment E1 baseline).

Order follows PLAN.md 5.5, stable parts first so the provider's prefix cache works: role and procedure, the
policy section rendered from policy.yaml, the company register, and then the case slice in the first user
message. The role text (single_role.md) describes the procedure and the tools only; rules come from the policy
section, so no rule is written twice.
"""

from __future__ import annotations

from pathlib import Path

from article15.company.models import RequestCase
from article15.company.register import Register
from article15.core.model import sha256_hex
from article15.policy.models import Policy
from article15.policy.render import render_policy_section
from article15.schemas import AgentName

SINGLE_ROLE_PATH = Path(__file__).with_name("single_role.md")


def single_role() -> str:
    return SINGLE_ROLE_PATH.read_text(encoding="utf-8").strip()


def system_prompt(policy: Policy, register: Register) -> str:
    return "\n\n".join([single_role(), render_policy_section(policy, AgentName.SINGLE), register.render()])


def prompt_hash(prompt: str) -> str:
    return sha256_hex(prompt)[:16]


def case_message(request: RequestCase, due: str, extended_due: str, notice_by: str) -> str:
    return "\n".join(
        [
            f"CASE {request.case_id}",
            f"Received on {request.received_on} by {request.channel.value} from {request.sender}.",
            f"Deadlines computed by the system: reply due {due}; an extension, if one is needed, must be "
            f"announced by {notice_by} and moves the deadline to {extended_due}.",
            "The request:",
            "<untrusted_request>",
            f"Subject: {request.subject_line}",
            request.text,
            "</untrusted_request>",
            "Start with intake.",
        ]
    )


def links_result(linked: int, not_linked: int) -> str:
    parts = [f"The requester has replied about the other address(es): {linked} confirmed and now searchable"]
    if not_linked:
        parts.append(f"{not_linked} not confirmed, so they stay out of the case")
    return (
        "; ".join(parts)
        + ". The case is back at IDENTITY_VERIFIED: search the four systems again, then call "
        "case_submit_discovery."
    )


def resumed(state: str) -> str:
    return (
        f"The case was interrupted and has been resumed. It is at {state}: the stages before it are finished "
        f"and "
        "recorded. Call case_get_status, then carry on from there."
    )


REPLY_ARRIVED = (
    "The requester has replied to your question; read the reply with notify_read_replies. The case is back "
    "at IDENTITY_CHECK: decide again with case_submit_identity."
)
