"""What the letter and the simulation stand on: the privacy register, the simulated requester, redaction
offsets, settings, and the single agent's policy section."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from article15.company.models import CaseKind, World
from article15.company.register import ART15_SECTIONS, load_register
from article15.desk.prompts import system_prompt
from article15.desk.redaction import (
    RedactionSubmission,
    find_all,
    foreign_order_numbers,
    mask,
    resolve,
    verify,
)
from article15.desk.requester import reply
from article15.policy.models import SINGLE_AGENT_ROLES, load_policy
from article15.policy.render import render_policy_section
from article15.schemas import AgentName, Art15Item, Language, RedactionCategory, RedactionSpan, System
from article15.settings import ModelSettings, SettingsError, load_dotenv

# --- register -----------------------------------------------------------------------------------------


def test_register_answers_every_article_15_item_in_both_languages() -> None:
    register = load_register()
    text = register.render()
    assert set(ART15_SECTIONS) == set(Art15Item)
    for item in Art15Item:
        assert text.count(f"[{item.value}]") == 2, item  # once per language
    assert "[fr]" in text and "[en]" in text
    assert "3 Place de Fontenoy, 75007 Paris" in text and "https://www.cnil.fr/fr/plaintes" in text
    assert "25 mois" in text and "25 months" in text


def test_every_register_entry_names_its_source() -> None:
    register = load_register()
    entries = [*register.purposes, *register.categories, *register.recipients]
    entries += [register.transfers, register.automated_decisions, register.data_source, register.rights]
    entries += [register.complaint, *(p.retention for p in register.purposes)]
    for entry in entries:
        assert entry.source.strip() and entry.fr.strip() and entry.en.strip()


def test_a_register_missing_an_item_does_not_load(tmp_path: Path) -> None:
    text = Path("src/article15/company/register.yaml").read_text(encoding="utf-8")
    broken = tmp_path / "register.yaml"
    broken.write_text(text.replace("\ncomplaint:", "\nunused_complaint:"), encoding="utf-8")
    with pytest.raises(ValueError):
        load_register(broken)


# --- the simulated requester -------------------------------------------------------------------------


def _truth(world: World, case_id: str):  # type: ignore[no-untyped-def]
    return next(t for t in world.truths if t.case_id == case_id)


def test_requester_replies_follow_what_they_know(world: World) -> None:
    people = {p.person_id: p for p in world.people}
    for truth in world.truths:
        if truth.expected_identity.value != "NEEDS_MORE_INFO":
            continue
        text = reply(truth, people, 1)
        assert text == reply(truth, people, 1)  # deterministic
        profile = truth.requester
        genuine = profile.person_id == truth.subject_id
        if genuine and profile.knows_order_reference and profile.knows_postcode:
            assert profile.knows_order_reference in text and profile.knows_postcode in text
        else:
            subject_orders = {o.order_id for o in world.orders if o.customer_id == truth.subject_id}
            assert not any(order in text for order in subject_orders), truth.case_id
        if truth.kind is CaseKind.IDENTITY_REPRESENTATIVE:
            assert "procuration" in text or "authorisation" in text


def test_an_impostor_signs_with_the_claimed_name(world: World) -> None:
    people = {p.person_id: p for p in world.people}
    truth = _truth(world, "A15-020")
    assert people[truth.subject_id].name in reply(truth, people, 1)


# --- redaction ----------------------------------------------------------------------------------------


def _span(start: int, end: int, cat: RedactionCategory = RedactionCategory.THIRD_PARTY) -> RedactionSpan:
    return RedactionSpan(
        system=System.HELPDESK, record_id="M1", field="body", start=start, end=end, category=cat, reason="r"
    )


def test_quotes_resolve_to_offsets_with_occurrences() -> None:
    fields = {(System.HELPDESK, "M1", "body"): "Anna called. Then Anna left."}
    q = {"system": "helpdesk", "record_id": "M1", "field": "body", "category": "third_party", "reason": "r"}
    plan, errors = resolve(RedactionSubmission.model_validate({"spans": [{**q, "text": "Anna"}]}), fields)
    assert not errors and plan and [(s.start, s.end) for s in plan.spans] == [(0, 4), (18, 22)]
    second = {"spans": [{**q, "text": "Anna", "occurrence": 2}]}
    plan, _ = resolve(RedactionSubmission.model_validate(second), fields)
    assert plan and [(s.start, s.end) for s in plan.spans] == [(18, 22)]
    assert verify(fields, plan, Language.EN) == ["'Anna' is still readable in helpdesk M1 body"]
    third = {"spans": [{**q, "text": "Anna", "occurrence": 3}]}
    _, errors = resolve(RedactionSubmission.model_validate(third), fields)
    assert errors == ["'Anna' occurs 2 time(s) in helpdesk M1 body"]


def test_mask_merges_overlapping_spans() -> None:
    assert mask("Anna Marie Smith", [_span(0, 10), _span(5, 16)], Language.FR) == "[masqué : tiers]"
    assert mask("call Bob now", [_span(5, 8)], Language.EN) == "call [redacted: third party] now"


@given(st.text(alphabet="ab ", max_size=40), st.text(alphabet="ab", min_size=1, max_size=3))
def test_masking_every_occurrence_leaves_none(text: str, quote: str) -> None:
    starts = find_all(text, quote)
    masked = mask(text, [_span(s, s + len(quote)) for s in starts], Language.EN)
    outside_markers = masked.replace("[redacted: third party]", "|")
    assert quote not in outside_markers
    # Touching occurrences ("aa", quote "a") merge into one marker, so there are at most as many markers.
    assert (outside_markers.count("|") >= 1) is bool(starts)
    assert outside_markers.count("|") <= len(starts)


# --- settings -----------------------------------------------------------------------------------------


def test_model_settings_need_a_key_except_for_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DEEPSEEK_API_KEY", "A15_PROVIDER", "A15_THINKING", "A15_DEEPSEEK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SettingsError, match="DEEPSEEK_API_KEY"):
        ModelSettings.from_env("deepseek")
    replay = ModelSettings.from_env("replay")
    assert replay.model == "deepseek-flash" and replay.thinking == "off"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("A15_THINKING", "loud")
    with pytest.raises(SettingsError, match="A15_THINKING"):
        ModelSettings.from_env("deepseek")


def test_dotenv_never_overrides_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('A15_X1="from file"\nA15_X2=file\n# comment\n', encoding="utf-8")
    monkeypatch.delenv("A15_X1", raising=False)
    monkeypatch.setenv("A15_X2", "shell")
    load_dotenv(env)
    import os

    assert os.environ["A15_X1"] == "from file" and os.environ["A15_X2"] == "shell"
    monkeypatch.delenv("A15_X1")


# --- policy for the single agent ----------------------------------------------------------------------


def test_the_single_agent_carries_every_rule_but_the_advisors() -> None:
    policy = load_policy()
    section = render_policy_section(policy, AgentName.SINGLE)
    for rule in policy.rules:
        expected = bool(SINGLE_AGENT_ROLES & set(rule.applies_to))
        assert (f"[{rule.id}]" in section) is expected, rule.id


def test_the_system_prompt_is_stable() -> None:
    policy, register = load_policy(), load_register()
    assert system_prompt(policy, register) == system_prompt(policy, register)


def test_another_customers_order_number_left_readable_is_caught() -> None:
    # Live runs hid the other customer's name but not their order number; the verifier only checked the spans
    # the agent chose, so code now checks for order numbers that are not the customer's own.
    key = (System.HELPDESK, "M000033", "body")
    text = (
        "I received a parcel from order FL-100514 addressed to [redacted: third party]. "
        "My order FL-100200 is late."
    )
    problems = foreign_order_numbers({key: text}, own_orders={"FL-100200"})
    assert len(problems) == 1 and "FL-100514" in problems[0] and "FL-100200" not in problems[0]
    assert foreign_order_numbers({key: text.replace("FL-100514", "[redacted]")}, {"FL-100200"}) == []
