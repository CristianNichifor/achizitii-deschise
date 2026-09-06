"""Map SEAP direct acquisitions onto OCDS releases.

OCDS models line items natively (`tender.items[]` / `awards[].items[]` with
`quantity`, `unit.name` and `unit.value`), which is exactly the shape SEAP already
publishes. We therefore emit OCDS rather than inventing a schema.

Field-by-field justification lives in `docs/ocds-mapping.md`.
"""

from __future__ import annotations

import re
from typing import Any

from . import config
from .normalize import fix_diacritics

# Organisation strings arrive in several shapes:
#   "9626572 - FUNDATIA DE SPRIJIN COMUNITAR"
#   "33203265 Expert Business Center SRL"
#   "RO 15437993 ROMSYSTEMS"       <- VAT-registered suppliers carry an RO prefix
#   "RO15169122 MEDISERV"          <- sometimes with no space
# The RO prefix is a VAT marker, not part of the CUI. Missing it loses the join key for
# most suppliers, so it is stripped explicitly.
_ORG_RE = re.compile(
    r"^\s*(?:RO\s*)?(?P<cui>\d{2,12})\s*[-–]?\s*(?P<name>.+?)\s*$", re.IGNORECASE
)

# "79311100-8 - Servicii de elaborare de studii (Rev.2)"
_CPV_RE = re.compile(r"(?P<code>\d{8})(?:-(?P<check>\d))?\s*[-–]?\s*(?P<label>.*)")

_CATEGORY = {
    "furnizare": "goods",
    "servicii": "services",
    "lucrari": "works",
    "lucrări": "works",
}


def split_org(raw: str | None) -> tuple[str | None, str | None]:
    """Split "CUI Name" into (cui, name). Returns (None, raw) when no CUI is present."""
    if not raw:
        return None, None
    m = _ORG_RE.match(fix_diacritics(raw))
    if not m:
        return None, fix_diacritics(raw).strip() or None
    return m.group("cui"), m.group("name").strip() or None


def parse_cpv(raw: Any) -> tuple[str | None, str | None]:
    """Extract (8-digit CPV code, label) from the several shapes SEAP uses."""
    if raw is None:
        return None, None
    if isinstance(raw, dict):
        # Item-level CPV: {"id": .., "text": "label", "localeKey": "79311100-8"}
        code_source = raw.get("localeKey") or raw.get("code") or ""
        label = raw.get("text") or None
        m = re.search(r"\d{8}", str(code_source))
        if m:
            return m.group(0), fix_diacritics(label) if label else None
        raw = label or ""
    m = _CPV_RE.search(str(raw))
    if not m:
        return None, None
    label = m.group("label").strip(" -–")
    return m.group("code"), fix_diacritics(label) or None


def _org_party(raw: str | None, role: str) -> dict[str, Any] | None:
    cui, name = split_org(raw)
    if not name:
        return None
    party: dict[str, Any] = {
        "id": f"RO-CUI-{cui}" if cui else f"RO-NAME-{abs(hash(name)) % 10**10}",
        "name": name,
        "roles": [role],
    }
    if cui:
        party["identifier"] = {"scheme": "RO-CUI", "id": cui, "legalName": name}
    return party


def _item(raw: dict[str, Any]) -> dict[str, Any]:
    """One OCDS item.

    `unit.value` is the price of ONE unit. In SEAP that is `itemClosingPrice` —
    verified empirically against the header total, see docs/ocds-mapping.md.
    """
    cpv_code, cpv_label = parse_cpv(raw.get("cpvCode"))
    unit_price = raw.get("itemClosingPrice")
    if unit_price is None:
        unit_price = raw.get("itemEstimatedPrice")

    item: dict[str, Any] = {
        "id": str(raw.get("directAcquisitionItemID") or raw.get("catalogItemID") or ""),
        "description": fix_diacritics(raw.get("catalogItemName") or ""),
        "quantity": raw.get("itemQuantity"),
    }
    long_desc = raw.get("catalogItemDescription")
    if long_desc:
        item["x_longDescription"] = fix_diacritics(long_desc)
    if cpv_code:
        item["classification"] = {
            "scheme": "CPV",
            "id": cpv_code,
            "description": cpv_label,
        }
    unit: dict[str, Any] = {}
    if raw.get("itemMeasureUnit"):
        unit["name"] = fix_diacritics(raw["itemMeasureUnit"])
    if unit_price is not None:
        unit["value"] = {"amount": unit_price, "currency": config.CURRENCY}
    if unit:
        item["unit"] = unit
    return item


def direct_acquisition_to_release(detail: dict[str, Any]) -> dict[str, Any]:
    """Build a single OCDS release from a `PublicDirectAcquisition/getView` payload.

    A direct acquisition is a completed purchase with no separate tender stage, so the
    release carries both `tender` (what was sought, at estimated value) and one `award`
    (what was bought, at closing value) and is tagged accordingly.
    """
    da_id = detail.get("directAcquisitionID") or detail.get("directAcquisitionId")
    ocid = f"{config.OCID_PREFIX}-da-{da_id}"

    buyer = _org_party(detail.get("contractingAuthority"), "buyer")
    supplier = _org_party(detail.get("supplier"), "supplier")
    parties = [p for p in (buyer, supplier) if p]

    cpv_code, cpv_label = parse_cpv(detail.get("cpvCode"))
    items = [_item(i) for i in (detail.get("directAcquisitionItems") or [])]

    contract_type = (detail.get("sysAcquisitionContractType") or {}).get("text") or ""
    category = _CATEGORY.get(contract_type.strip().lower())

    state = (detail.get("sysDirectAcquisitionState") or {}).get("text") or ""

    tender: dict[str, Any] = {
        "id": str(detail.get("uniqueIdentificationCode") or da_id),
        "title": fix_diacritics(detail.get("directAcquisitionName") or ""),
        "procurementMethod": "direct",
        "procurementMethodDetails": "Achizitie directa",
        "items": items,
    }
    if detail.get("directAcquisitionDescription"):
        tender["description"] = fix_diacritics(detail["directAcquisitionDescription"])
    if category:
        tender["mainProcurementCategory"] = category
    if detail.get("estimatedValue") is not None:
        tender["value"] = {"amount": detail["estimatedValue"], "currency": config.CURRENCY}
    if cpv_code:
        tender["classification"] = {"scheme": "CPV", "id": cpv_code, "description": cpv_label}

    release: dict[str, Any] = {
        "ocid": ocid,
        "id": f"{da_id}-{(detail.get('finalizationDate') or '')[:10]}",
        "date": detail.get("publicationDate"),
        "tag": ["tender", "award"],
        "initiationType": "tender",
        "language": "ro",
        "parties": parties,
        "tender": tender,
    }
    if buyer:
        release["buyer"] = {"id": buyer["id"], "name": buyer["name"]}

    if detail.get("closingValue") is not None:
        award: dict[str, Any] = {
            "id": str(detail.get("daAwardNoticeID") or da_id),
            "status": "active" if "acceptat" in state.lower() else "pending",
            "date": detail.get("finalizationDate"),
            "value": {"amount": detail["closingValue"], "currency": config.CURRENCY},
            "items": items,
        }
        if supplier:
            award["suppliers"] = [{"id": supplier["id"], "name": supplier["name"]}]
        release["awards"] = [award]

    return release


def release_package(releases: list[dict[str, Any]], published_date: str) -> dict[str, Any]:
    """Wrap releases in an OCDS release package."""
    return {
        "uri": f"{config.CONTACT}/releases/{published_date}",
        "version": "1.1",
        "publishedDate": published_date,
        "publisher": config.PUBLISHER,
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "publicationPolicy": f"{config.CONTACT}/blob/main/METHODOLOGY.md",
        "releases": releases,
    }
