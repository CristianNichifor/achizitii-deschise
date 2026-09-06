"""Propose synonym clusters for human curation.

Romanian buyers describe identical goods differently, and sometimes file them under
different CPV codes. In the archive, `balama aplicata mobilier` appears under both
`39000000` and `39200000`; the brief's motivating example, "bănci parc" versus
"mobilier odihnă exterior", is the same problem. Deterministic grouping
(`achizitii.produs`) collapses casing, boilerplate and brand, but it cannot know that
two different phrases mean the same object.

That residue is a genuine fit for a language model — and the only one in this project.

**This module never runs as part of the published pipeline.** It writes a PROPOSAL file
for a human to review; accepted entries are committed to `data/cpv_aliases.yml`, and the
pipeline reads only that file. Consequences, all deliberate:

- Every published figure stays reproducible from checked-in data.
- The inference provider can disappear without changing any result. Hetzner's own terms
  state that "performance and availability are not guaranteed" and make no promise the
  experiment becomes a product.
- A model's output is never a finding. It is a suggestion, subject to review, exactly
  like a contributor's pull request.

The client speaks the OpenAI chat-completions dialect, so any compatible endpoint works.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://inference.hetzner.com/api/v1"
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"

# Hetzner documents 10 requests per 60s per key, and returns 429 on violation. One
# request every 6.5 seconds stays under that with margin; being a polite guest on a free
# experimental service matters more here than throughput.
MIN_REQUEST_INTERVAL = 6.5

RETRY_ON_429 = 3
"""429 means we were too fast — worth backing off and retrying."""

RETRY_ON_503 = 1
"""503 means no GPU is free. Retrying rarely helps and only adds load."""


class InferenceUnavailable(RuntimeError):
    """The endpoint has no capacity. Expected, not exceptional."""


@dataclass
class InferenceClient:
    """Minimal OpenAI-compatible client with the provider's documented limits applied."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = 180.0
    _last_call: float = field(default=0.0, repr=False)

    @classmethod
    def from_env(cls) -> InferenceClient:
        """Configuration from environment.

        The key is read from the environment and never logged. Resolve it from a secret
        manager at the call site, e.g.
        `HZ=$(op read op://vault/item/credential) achizitii cluster ...`.
        """
        key = os.environ.get("INFERENCE_API_KEY") or os.environ.get("HZ") or ""
        if not key:
            raise RuntimeError(
                "no API key: set INFERENCE_API_KEY (or use --dry-run, which needs none)"
            )
        return cls(
            api_key=key,
            base_url=os.environ.get("INFERENCE_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("INFERENCE_MODEL", DEFAULT_MODEL),
        )

    def _throttle(self) -> None:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def chat(self, prompt: str, max_tokens: int = 1200) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                # Near-zero: this is a clustering task, not a creative one, and a
                # reviewer comparing two runs should see the same proposals.
                "temperature": 0.1,
            }
        ).encode()

        for attempt in range(RETRY_ON_429 + 1):
            self._throttle()
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.load(resp)
                return payload["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    backoff = 10 * (attempt + 1)
                    log.warning("rate limited, backing off %ds", backoff)
                    time.sleep(backoff)
                    continue
                if exc.code == 503:
                    raise InferenceUnavailable(
                        "endpoint reports no serving capacity (503). The provider states "
                        "availability is not guaranteed; try again later."
                    ) from exc
                raise
            except TimeoutError as exc:
                raise InferenceUnavailable("request timed out") from exc
        raise InferenceUnavailable("still rate limited after retries")


PROMPT = """Grupeaza produse din achizitii publice romanesti.

Primesti chei de forma COD_CPV|descriere. Grupeaza DOAR cheile care descriu EXACT
acelasi tip de produs, chiar daca au coduri CPV diferite sau formulari diferite.

REGULI STRICTE:
1. Grupeaza doar produse identice ca natura (ex: "banci parc" si "mobilier odihna
   exterior" sunt acelasi produs). NU grupa produse doar pentru ca sunt din aceeasi
   categorie generala.
2. Foloseste EXCLUSIV chei din lista primita. Nu inventa chei si nu le modifica.
3. Daca o cheie nu are pereche clara, NU o include in niciun grup.
4. Un grup are minim 2 chei.
5. Raspunde EXCLUSIV cu JSON valid, fara text suplimentar:
{"grupuri":[{"eticheta":"nume scurt","chei":["cheie1","cheie2"],"motiv":"de ce sunt identice"}]}

CHEI:
%s
"""


@dataclass(frozen=True)
class Proposal:
    label: str
    keys: tuple[str, ...]
    reason: str


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply.

    Small models wrap JSON in prose or fences despite instructions, so the object is
    located rather than assumed to be the whole reply.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else ""
    if not candidate:
        raise ValueError("no JSON object in reply")
    return json.loads(candidate)


def parse_proposals(reply: str, allowed: set[str]) -> tuple[list[Proposal], list[str]]:
    """Validate a reply against the keys actually sent.

    Returns (proposals, rejections). A model may invent or reword keys; anything not
    present verbatim in the input is discarded rather than corrected, on the same
    principle as the `sursa_text` check in the OCDS extractor — an unverifiable output
    is treated as absent, never as approximately right.
    """
    data = _extract_json(reply)
    proposals: list[Proposal] = []
    rejections: list[str] = []

    for group in data.get("grupuri", []):
        keys = [str(k) for k in group.get("chei", [])]
        unknown = [k for k in keys if k not in allowed]
        if unknown:
            rejections.append(f"hallucinated keys {unknown} in {group.get('eticheta')!r}")
            continue
        unique = tuple(dict.fromkeys(keys))
        if len(unique) < 2:
            rejections.append(f"group {group.get('eticheta')!r} has fewer than 2 keys")
            continue
        proposals.append(
            Proposal(
                label=str(group.get("eticheta") or "").strip() or "fara eticheta",
                keys=unique,
                reason=str(group.get("motiv") or "").strip(),
            )
        )
    return proposals, rejections


def build_prompt(keys: list[str]) -> str:
    return PROMPT % "\n".join(keys)
