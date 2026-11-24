"""The LLM judge for the letter's quality (PLAN.md 8.3): clarity, accuracy and tone, 1 to 5, with a written
rubric (judge_rubric.md).

Only the letter is judged this way; everything else is graded exactly (graders.py). The judge sees the letter,
its language, the record counts of the copy and Ferlane's register, and nothing else about the case. Its
calls go through the same provider layer as the agents, so they are recorded and replayed like any other
(`<case>.judge.jsonl` next to the case's cassette), and cost is counted.

The judge must agree with a person before its scores mean anything: `agreement` compares its scores with the
ratings a person gives on the golden 20 (evals/golden/ratings.yaml).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from article15.company.register import Register
from article15.core.agent import Agent, Budget, Caps, StopReason
from article15.core.mcp_tools import escape_untrusted
from article15.core.model import Message, Thinking
from article15.core.provider import Provider
from article15.core.tools import SubmitTool, ToolManager
from article15.schemas import Strict

RUBRIC_PATH = Path(__file__).with_name("judge_rubric.md")
JUDGE_CAPS = Caps(max_calls=4, max_input_tokens=60_000, max_seconds=120.0, max_cost_usd=0.02)
DIMENSIONS = ("clarity", "accuracy", "tone")
# What "the judge agrees with the person" means, per dimension. A judge that always answers the same score can
# look close on mean difference when most letters are decent, so it must also beat that constant and catch the
# bad letters: of the letters the person scored LOW or less, the share the judge also scored LOW or less.
MAX_MEAN_ABS_DIFF = 0.75
MIN_CATCH_RATE = 0.8
LOW = 2
CONSTANT_BASELINE = 4.0


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
                + escape_untrusted(f"Subject: {letter['subject_line']}\n\n{letter['body']}")
                + "\n</untrusted_data>"
            ),
        },
    ]


async def judge_letter(
    case_dir: Path, register: Register, provider: Provider, model: str
) -> tuple[dict[str, float] | None, str, float]:
    """Scores for the letter in `case_dir/pack.json`, the judge's notes (why it took points off), and its
    cost. Scores are None when there is no letter or the judge did not return valid scores within its caps.

    Only the letter that goes with a copy of the data is judged: it is the only letter a model writes. The
    Art. 12(4) refusal letter is built by code from fixed texts (desk/refusal.py) and checked by a guard."""
    path = case_dir / "pack.json"
    if not path.exists():
        return None, "", 0.0
    pack = json.loads(path.read_text(encoding="utf-8"))
    tools = ToolManager([SubmitTool(LetterScores, description="Return the three scores and your notes.")])
    agent = Agent(
        name="judge", provider=provider, tools=tools, model=model, thinking=Thinking.OFF, max_tokens=800
    )
    budget = Budget(caps=JUDGE_CAPS)
    run = await agent.run(judge_messages(pack, register), budget)
    if run.stop_reason is not StopReason.COMPLETED or not isinstance(run.final, LetterScores):
        return None, "", budget.cost_usd
    s = run.final
    scores = {"clarity": float(s.clarity), "accuracy": float(s.accuracy), "tone": float(s.tone)}
    return scores, s.notes, budget.cost_usd


def agreement(
    judge: dict[str, dict[str, float]], person: dict[str, dict[str, float]]
) -> dict[str, float | bool | None]:
    """How close the judge is to the person, per dimension, over the letters both have scored:
    - `mean_abs_diff`, and `within_one` (the share within one point);
    - `baseline_mean_abs_diff`: the same for a judge that always answers CONSTANT_BASELINE;
    - `catch_rate`: of the letters the person scored LOW or less, the share the judge also did (None if none);
    - `agrees`: mean_abs_diff <= MAX_MEAN_ABS_DIFF, below the baseline's, and catch_rate >= MIN_CATCH_RATE
      (a catch rate with no bad letter to catch does not count against it).
    `agrees` overall is True only when every dimension agrees."""
    common = sorted(set(judge) & set(person))
    out: dict[str, float | bool | None] = {"letters": float(len(common))}
    overall: bool | None = bool(common) or None
    for d in DIMENSIONS:
        pairs = [(judge[c][d], person[c][d]) for c in common if d in judge[c] and d in person[c]]
        if not pairs:
            for k in ("mean_abs_diff", "within_one", "baseline_mean_abs_diff", "catch_rate", "agrees"):
                out[f"{d}_{k}"] = None
            overall = None
            continue
        mad = sum(abs(a - b) for a, b in pairs) / len(pairs)
        baseline = sum(abs(CONSTANT_BASELINE - b) for _, b in pairs) / len(pairs)
        bad = [(a, b) for a, b in pairs if b <= LOW]
        catch = sum(a <= LOW for a, _ in bad) / len(bad) if bad else None
        agrees = mad <= MAX_MEAN_ABS_DIFF and mad < baseline and (catch is None or catch >= MIN_CATCH_RATE)
        out[f"{d}_mean_abs_diff"] = mad
        out[f"{d}_within_one"] = sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs)
        out[f"{d}_baseline_mean_abs_diff"] = baseline
        out[f"{d}_catch_rate"] = catch
        out[f"{d}_agrees"] = agrees
        if overall is not None:
            overall = overall and agrees
    out["agrees"] = overall
    return out
