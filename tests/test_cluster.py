"""Tests for the synonym-proposal helper.

The module exists to suggest clusters for human review. Nothing it produces reaches a
published figure without a person committing it, so the tests concentrate on the
guard that makes review trustworthy: a model must not be able to introduce a key that
was never sent.
"""

from __future__ import annotations

import json

import pytest

from achizitii.cluster import (
    MIN_REQUEST_INTERVAL,
    Proposal,
    build_prompt,
    parse_proposals,
)

KEYS = {
    "39113600|banci parc",
    "39113600|mobilier odihna exterior",
    "30213100|laptop|dell",
    "30213100|laptop|hp",
}


def _reply(groups: list[dict]) -> str:
    return json.dumps({"grupuri": groups}, ensure_ascii=False)


class TestHallucinationGuard:
    def test_unknown_key_rejects_the_whole_group(self) -> None:
        """A key that was never sent cannot be verified, so the group is discarded
        rather than partially accepted — the same rule as `sursa_text` in the OCDS
        extractor."""
        reply = _reply([
            {"eticheta": "banci", "chei": ["39113600|banci parc", "39113600|inventat"],
             "motiv": "x"}
        ])
        proposals, rejections = parse_proposals(reply, KEYS)
        assert proposals == []
        assert rejections and "hallucinated" in rejections[0]

    def test_reworded_key_is_not_accepted(self) -> None:
        """Near-misses are rejected, not corrected."""
        reply = _reply([
            {"eticheta": "banci", "chei": ["39113600|banci parc", "39113600|banci-parc"],
             "motiv": "x"}
        ])
        proposals, _ = parse_proposals(reply, KEYS)
        assert proposals == []

    def test_valid_group_is_accepted(self) -> None:
        reply = _reply([
            {"eticheta": "banci parc",
             "chei": ["39113600|banci parc", "39113600|mobilier odihna exterior"],
             "motiv": "acelasi produs"}
        ])
        proposals, rejections = parse_proposals(reply, KEYS)
        assert rejections == []
        assert len(proposals) == 1
        assert isinstance(proposals[0], Proposal)
        assert len(proposals[0].keys) == 2

    def test_single_key_group_rejected(self) -> None:
        reply = _reply([{"eticheta": "solo", "chei": ["39113600|banci parc"], "motiv": ""}])
        proposals, rejections = parse_proposals(reply, KEYS)
        assert proposals == []
        assert "fewer than 2" in rejections[0]

    def test_duplicate_keys_collapse(self) -> None:
        reply = _reply([
            {"eticheta": "dup",
             "chei": ["39113600|banci parc", "39113600|banci parc",
                      "39113600|mobilier odihna exterior"],
             "motiv": ""}
        ])
        proposals, _ = parse_proposals(reply, KEYS)
        assert len(proposals[0].keys) == 2


class TestReplyParsing:
    def test_handles_fenced_json(self) -> None:
        """Small models wrap JSON in fences despite instructions."""
        inner = _reply([{"eticheta": "a",
                         "chei": ["30213100|laptop|dell", "30213100|laptop|hp"],
                         "motiv": ""}])
        proposals, _ = parse_proposals(f"Iata rezultatul:\n```json\n{inner}\n```\n", KEYS)
        assert len(proposals) == 1

    def test_handles_prose_around_json(self) -> None:
        inner = _reply([{"eticheta": "a",
                         "chei": ["30213100|laptop|dell", "30213100|laptop|hp"],
                         "motiv": ""}])
        proposals, _ = parse_proposals(f"Sigur! {inner} Sper ca ajuta.", KEYS)
        assert len(proposals) == 1

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="no JSON"):
            parse_proposals("nu am gasit nimic", KEYS)

    def test_empty_group_list(self) -> None:
        proposals, rejections = parse_proposals(_reply([]), KEYS)
        assert proposals == [] and rejections == []


class TestPromptAndLimits:
    def test_prompt_contains_every_key(self) -> None:
        prompt = build_prompt(sorted(KEYS))
        for k in KEYS:
            assert k in prompt

    def test_request_interval_respects_documented_limit(self) -> None:
        """The provider documents 10 requests per 60s and returns 429 on violation."""
        assert MIN_REQUEST_INTERVAL >= 6.0
