"""The LLM judge for the letter's quality (PLAN.md 8.3): clarity, accuracy and tone, 1 to 5, with a written
rubric (judge_rubric.md).

Only the letter is judged this way; everything else is graded exactly (graders.py). The judge sees the letter,
its language, the record counts of the copy and Ferlane's register, and nothing else about the case. Its
calls go through the same provider layer as the agents, so they are recorded and replayed like any other
(`<case>.judge.jsonl` next to the case's cassette), and cost is counted.

The judge must agree with a person before its scores mean anything: `agreement` compares its scores with the
project owner's ratings on the golden 20 (evals/golden/ratings.yaml).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from article15.company.register import Register
from article15.core.agent import Agent, Budget, Caps, StopReason
from article15.core.model import Message, Thinking
from article15.core.provider import Provider
from article15.core.tools import SubmitTool, ToolManager
from article15.schemas import Strict

RUBRIC_PATH = Path(__file__).with_name("judge_rubric.md")
JUDGE_CAPS = Caps(max_calls=4, max_input_tokens=60_000, max_seconds=120.0, max_cost_usd=0.02)
DIMENSIONS = ("clarity", "accuracy", "tone")


class LetterScores(Strict):
    clarity: int = Field(ge=1, le=5)
    accuracy: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    notes: str = Field(default="", description="One short sentence per score below 5.")


def rubric() -> str:
    return RUBRIC_PATH.read_text(encoding="utf-8")


def judge_messages(pack: dict[str, object], register: Register) -> list[Message]:
    letter = pack["letter"]
    assert isinstance(letter, dict)
    counts = pack.get("record_counts", {})
    return [
        {"role": "system", "content": rubric() + "\n\n" + register.render()},
        {
            "role": "user",
            "content": (
                f"Language required: {pack['language']}\n"
                f"Records in the attached copy, per system: {json.dumps(counts, sort_keys=True)}\n"
                '<untrusted_data source="letter">\n'
                f"Subject: {letter['subject_line']}\n\n{letter['body']}\n"
                "</untrusted_data>"
            ),
        },
    ]


async def judge_letter(
    case_dir: Path, register: Register, provider: Provider, model: str
) -> tuple[dict[str, float] | None, float]:
    """Scores for the letter in `case_dir/pack.json`, and the judge's cost. None when there is no letter or
    the judge did not return valid scores within its caps."""
    path = case_dir / "pack.json"
    if not path.exists():
        return None, 0.0
    pack = json.loads(path.read_text(encoding="utf-8"))
    tools = ToolManager([SubmitTool(LetterScores, description="Return the three scores and your notes.")])
    agent = Agent(
        name="judge", provider=provider, tools=tools, model=model, thinking=Thinking.OFF, max_tokens=800
    )
    budget = Budget(caps=JUDGE_CAPS)
    run = await agent.run(judge_messages(pack, register), budget)
    if run.stop_reason is not StopReason.COMPLETED or not isinstance(run.final, LetterScores):
        return None, budget.cost_usd
    s = run.final
    return {
        "clarity": float(s.clarity),
        "accuracy": float(s.accuracy),
        "tone": float(s.tone),
    }, budget.cost_usd


def agreement(
    judge: dict[str, dict[str, float]], person: dict[str, dict[str, float]]
) -> dict[str, float | None]:
    """How close the judge is to the person, per dimension: the mean absolute difference and the share of
    letters scored within one point. Only letters both have scored count."""
    common = sorted(set(judge) & set(person))
    out: dict[str, float | None] = {"letters": float(len(common))}
    for d in DIMENSIONS:
        pairs = [(judge[c][d], person[c][d]) for c in common if d in judge[c] and d in person[c]]
        if not pairs:
            out[f"{d}_mean_abs_diff"] = out[f"{d}_within_one"] = None
            continue
        out[f"{d}_mean_abs_diff"] = sum(abs(a - b) for a, b in pairs) / len(pairs)
        out[f"{d}_within_one"] = sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs)
    return out
