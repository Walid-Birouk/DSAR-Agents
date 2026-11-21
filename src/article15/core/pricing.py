"""Model prices and the cost of one call.

Sources, read 2026-11-21:
* DeepSeek: https://api-docs.deepseek.com/quick_start/pricing . Prices below are the peak prices in USD per
  1M tokens. "Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday, excluding Chinese
  public holidays"; all other hours are off-peak at half the peak price.
* Mistral: https://docs.mistral.ai/inference/pricing and /inference/regional-inference (read 2026-11-20 for
  PLAN.md 15.3). The EU endpoint (api.eu.mistral.ai) costs 1.1 times list price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import cache

import holidays

from article15.core.model import Usage

PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    cache_hit: float
    cache_miss: float
    output: float

    def scaled(self, factor: float) -> Price:
        return Price(self.cache_hit * factor, self.cache_miss * factor, self.output * factor)


DEEPSEEK_PEAK: dict[str, Price] = {
    "deepseek-flash": Price(cache_hit=0.006, cache_miss=0.30, output=1.20),
    "deepseek-v4-pro": Price(cache_hit=0.044, cache_miss=1.32, output=3.96),
}
MISTRAL_LIST: dict[str, Price] = {
    "mistral-small-2603": Price(cache_hit=0.015, cache_miss=0.15, output=0.60),
    "mistral-large-4": Price(cache_hit=0.07, cache_miss=0.70, output=2.10),
}
MISTRAL_EU_FACTOR = 1.1
DEEPSEEK_OFF_PEAK_FACTOR = 0.5
DEEPSEEK_PEAK_WINDOWS_UTC = ((time(1), time(4)), (time(6), time(10)))
CHINA_OFFSET = timedelta(hours=8)


class UnknownModelPrice(KeyError):
    pass


@cache
def _china_holidays(year: int) -> holidays.HolidayBase:
    return holidays.country_holidays("CN", years=year)


def deepseek_is_peak(at: datetime) -> bool:
    utc = at.astimezone(UTC)
    if utc.weekday() >= 5:
        return False
    # Holidays are calendar days in China (UTC+8); the peak windows fall on the same day there.
    china_day = (utc + CHINA_OFFSET).date()
    if china_day in _china_holidays(china_day.year):
        return False
    t = utc.time()
    return any(start <= t < end for start, end in DEEPSEEK_PEAK_WINDOWS_UTC)


def price_for(provider: str, model: str, at: datetime, eu_endpoint: bool = False) -> Price:
    if provider == "deepseek":
        if model not in DEEPSEEK_PEAK:
            raise UnknownModelPrice(f"no price for deepseek model {model}")
        peak = DEEPSEEK_PEAK[model]
        return peak if deepseek_is_peak(at) else peak.scaled(DEEPSEEK_OFF_PEAK_FACTOR)
    if provider == "mistral":
        if model not in MISTRAL_LIST:
            raise UnknownModelPrice(f"no price for mistral model {model}")
        listed = MISTRAL_LIST[model]
        return listed.scaled(MISTRAL_EU_FACTOR) if eu_endpoint else listed
    raise UnknownModelPrice(f"no prices for provider {provider}")


def cost_usd(usage: Usage, price: Price) -> float:
    return (
        usage.cache_hit_tokens * price.cache_hit
        + usage.cache_miss_tokens * price.cache_miss
        + usage.output_tokens * price.output
    ) / PER_MILLION
