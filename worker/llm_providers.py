"""Write Gate judge providers — NIM / Gemini with a local rule-based fallback."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import settings
from app.domain.policy import AdmissionVerdict, JudgeVerdict


class JudgeProvider(Protocol):
    name: str

    def judge(self, content: str) -> JudgeVerdict: ...


@dataclass
class LocalRuleJudge:
    """Deterministic fallback used when remote providers are unavailable."""

    name: str = "local_rule_judge"

    def judge(self, content: str) -> JudgeVerdict:
        lowered = content.lower()
        if lowered.startswith("the user"):
            return JudgeVerdict(
                verdict=AdmissionVerdict.ADMIT,
                rationale="Candidate states a user preference or fact.",
                provider=self.name,
            )
        if "assistant" in lowered:
            return JudgeVerdict(
                verdict=AdmissionVerdict.REJECT,
                rationale="Candidate describes assistant output, not a user fact.",
                provider=self.name,
            )
        if "article" in lowered or "discussed software" in lowered:
            return JudgeVerdict(
                verdict=AdmissionVerdict.REJECT,
                rationale="Candidate cites third-party content, not a user fact.",
                provider=self.name,
            )
        return JudgeVerdict(
            verdict=AdmissionVerdict.REJECT,
            rationale="Candidate does not match user-fact heuristics.",
            provider=self.name,
        )


@dataclass
class HttpJudgeProvider:
    """Remote LLM judge with an explicit outbound timeout."""

    name: str
    url: str
    api_key: str
    model: str
    timeout_seconds: float

    def judge(self, content: str) -> JudgeVerdict:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a memory admission judge. Reply with JSON only: "
                        '{"verdict":"admit"|"reject","rationale":"..."}'
                    ),
                },
                {"role": "user", "content": content},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(self.url, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        text = body["choices"][0]["message"]["content"]
        parsed = json.loads(text)
        verdict = AdmissionVerdict(parsed["verdict"])
        return JudgeVerdict(
            verdict=verdict,
            rationale=str(parsed.get("rationale", "")),
            provider=self.name,
        )


def _remote_providers() -> list[JudgeProvider]:
    providers: list[JudgeProvider] = []
    if settings.nim_api_url and settings.nim_api_key:
        providers.append(
            HttpJudgeProvider(
                name="nim",
                url=settings.nim_api_url,
                api_key=settings.nim_api_key,
                model="meta/llama-3.1-8b-instruct",
                timeout_seconds=settings.write_gate_provider_timeout_seconds,
            )
        )
    if settings.gemini_api_url and settings.gemini_api_key:
        providers.append(
            HttpJudgeProvider(
                name="gemini",
                url=settings.gemini_api_url,
                api_key=settings.gemini_api_key,
                model="gemini-2.0-flash",
                timeout_seconds=settings.write_gate_provider_timeout_seconds,
            )
        )
    return providers


def get_judge_chain() -> list[JudgeProvider]:
    chain = _remote_providers()
    chain.append(LocalRuleJudge())
    return chain


def judge_with_fallback(content: str) -> JudgeVerdict:
    for provider in get_judge_chain():
        try:
            return provider.judge(content)
        except Exception:
            continue
    return LocalRuleJudge().judge(content)


def judge_with_fallback_and_delay(content: str, delay_seconds: float) -> JudgeVerdict:
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    return judge_with_fallback(content)
