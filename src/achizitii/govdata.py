"""Bulk ingest of the ANAP/ADR quarterly exports published on data.gov.ro.

These files answer a different question from the SEAP API. The API carries line items
and therefore unit prices ("was this a fair price?"). The bulk exports carry no
quantities at all, but cover every acquisition and every contract, which is what
behavioural indicators need ("did they follow the rules? who benefits?").

Two things make this messy and both are handled here:

1. **Format drift.** 2017 is caret-delimited CSV, 2020 is XLSX, 2023 is XLS, 2026 mixes
   XLSX with ODS files served under an `.xlsx` name. The declared `format` field on
   data.gov.ro is unreliable, so content is sniffed from magic bytes.

2. **Schema drift.** Column names changed completely between years. The 2017 export uses
   `Castigator` / `AutoritateContractantaCUI` / `ValoareRON`; the 2026 export uses
   `Ofertant castigator` / `CUI autoritate contractanta` / `Valoare achizitie (RON)`.
   Older files are in places *richer* — 2017 includes supplier locality and address,
   which later exports dropped.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import CONTACT
from .normalize import fold

log = logging.getLogger(__name__)

CKAN = "https://data.gov.ro/api/3/action/package_show"

# Candidate dataset slugs, tried in order. 2019 is published under a MISSPELLED slug
# ("achiziti-publice-2019", one "i" short) on data.gov.ro itself, so a single template
# silently loses that entire year. Verified 2026-09-06.
DATASET_SLUGS = (
    "achizitii-publice-{year}",
    "achiziti-publice-{year}",
)


# --------------------------------------------------------------------------- tables

@dataclass(frozen=True)
class TableSpec:
    """One logical table, and how to recognise its resource on data.gov.ro."""

    key: str
    match: re.Pattern[str]
    columns: dict[str, tuple[str, ...]]
    """canonical name -> accepted source headers (matched after `fold`)"""


def _c(*names: str) -> tuple[str, ...]:
    return names


ACHIZITII_DIRECTE = TableSpec(
    key="achizitii_directe",
    # "Achiziții directe", "Achizitii Directe T I 2024", "Cumparari directe 2017 - T1"
    match=re.compile(r"(?i)\b(achizi[tț]ii|cump[aă]r[aă]ri)\s+directe"),
    columns={
        "autoritate": _c(
            "autoritate contractanta", "autoritatecontractanta", "nume ac",
            "denumire ac",
        ),
        "autoritate_cui": _c(
            "cui autoritate contractanta", "autoritatecontractantacui", "cui ac"
        ),
        "nr_achizitie": _c(
            "numar achizitie directa", "numar achizitie", "numaranunt", "numar anunt",
            "numar achizite",  # typo in the 2023 Q2 source
        ),
        "data_publicare": _c(
            "data publicare", "dataanunt", "data anunt", "data publicare achizitie"
        ),
        "data_finalizare": _c(
            "data finalizare", "datacontract", "data contract",
            "data atribuire achizitie",
        ),
        "denumire": _c("denumire achizitie", "denumire", "descriere", "titlucontract"),
        "cpv": _c("cod cpv", "cpvcode"),
        # NOT "cpvcodeid": that column holds a numeric internal id (39831240 -> 15113),
        # not a label. Mapping it here filled the field with meaningless integers.
        "cpv_denumire": _c(
            "denumire cpv", "cpvcodename", "denumire cod cpv", "nume cpv"
        ),
        "tip_contract": _c("tip contract", "tipincheierecontract"),
        "valoare_ron": _c(
            "valoare achizitie ron", "valoare achizitie", "valoare atribuita ron",
            "valoareron", "valoare",
        ),
        "furnizor": _c("ofertant castigator", "castigator", "ofertant"),
        "furnizor_cui": _c(
            "cui ofertant castigator", "castigatorcui", "cui castigator", "cui ofertant"
        ),
        # present only in the older exports
        "furnizor_localitate": _c("castigatorlocalitate", "oras castigator"),
        "tip_procedura": _c("tip procedura", "tipprocedura"),
    },
)

CONTRACTE = TableSpec(
    key="contracte",
    match=re.compile(r"(?i)^(?!.*subsecvent).*\bcontracte\b(?!.*modificare)"),
    columns={
        "autoritate": _c(
            "autoritate contractanta", "autoritatecontractanta", "denumire ac"
        ),
        "autoritate_cui": _c(
            "cui autoritate contractanta", "autoritatecontractantacui", "cui ac",
            # Typo in the 2022 Q4 source: "conractanta". One 2022 quarter shortens the
            # header to a bare "CUI"; that is under MIN_PREFIX_ALIAS, so it is matched
            # exactly and cannot swallow "CUI ofertant".
            "cui autoritate conractanta", "cui",
        ),
        "tip_procedura": _c("tip procedura", "tipprocedura"),
        "nr_anunt_initiere": _c(
            "numar anunt initiere", "numaranuntparticipare", "numar anunt ai"
        ),
        "nr_anunt_atribuire": _c("numar anunt atribuire", "numaranuntatribuire", "numaranunt"),
        "data_publicare": _c("data publicare", "dataanuntatribuire", "dataanunt"),
        "tip_contract": _c("tip contract", "tipcontract"),
        "criteriu_atribuire": _c(
            "tip criterii de atribuire", "criteriu de atribuire",
            "tipcriteriiatribuire", "tip criteriu de atribuire",
        ),
        "cpv": _c("cod cpv", "cpvcode", "cpv code"),
        "cpv_denumire": _c("denumire cpv", "cpvcodename"),
        "nr_lot": _c("numar lot"),
        # WITHOUT THESE TWO, CONTRACT VALUES CANNOT BE SUMMED SAFELY.
        # 95% of contract rows are framework agreements (217,407 of 229,089 in H1 2026),
        # averaging 55.6 contracts per notice, and 128,920 of those are call-offs under
        # a framework whose headline value is also present. Adding them together
        # double-counts massively: 534.4 bn RON of "acord-cadru" against 176.9 bn of
        # actual public procurement contracts.
        "tip_incheiere": _c(
            # "Tip inchiere contract" is a typo in the 2024 source.
            "tip incheiere contract", "tipincheierecontract", "tip inchiere contract",
        ),
        "incheiat_prin": _c("incheiat prin"),
        # Number of offers received. Present only in the 2016-2018 era exports and
        # dropped from later ones — which is unfortunate, because single-bidder rate is
        # the strongest indicator in the procurement-corruption literature. Its absence
        # from the modern exports is why that indicator is limited to the early years.
        "numar_oferte": _c("numaroferteprimite", "numar oferte primite", "numar oferte"),
        # The early exports carry the estimate on the contract row itself, so
        # estimate-versus-award needs no join for those years.
        "valoare_estimata_ron": _c(
            "valoareestimataparticipare", "valoare estimata", "valoare estimata ron"
        ),
        "licitatie_electronica": _c("culicitatieelectronica", "cu licitatie electronica"),
        "subcontractat": _c("subcontractat", "cu subcontractare"),
        "data_contract": _c("data contract", "datacontract"),
        "nr_contract": _c("numar contract", "numarcontract"),
        "valoare_ron": _c(
            "valoare contract ron", "valoare atribuita ron", "valoareron", "valoare"
        ),
        # "Catigator" is a typo in the 2022 Q4 source, not a variant spelling.
        "furnizor": _c(
            "ofertant castigator", "castigator", "ofertant", "catigator"
        ),
        "furnizor_cui": _c(
            "cui ofertant castigator", "castigatorcui", "cui ofertant", "cui of",
            "cui castigator",
        ),
    },
)

FARA_ANUNT = TableSpec(
    key="fara_anunt",
    match=re.compile(r"(?i)f[aă]r[aă]\s+anun[tț]\s+de\s+ini[tț]iere"),
    columns={
        "autoritate": _c("autoritate contractanta", "denumire ac"),
        "autoritate_cui": _c("cui autoritate contractanta", "cui"),
        "tip_procedura": _c("tip procedura"),
        "nr_anunt_atribuire": _c("numar anunt atribuire"),
        "data_publicare": _c("data publicare", "data anunt atribuire"),
        "tip_contract": _c("tip contract"),
        "criteriu_atribuire": _c("criteriu de atribuire", "tip criterii de atribuire"),
        "cpv": _c("cod cpv"),
        "cpv_denumire": _c("denumire cpv"),
        # "Dtaa contract" is a typo in the 2024 source, not a variant spelling.
        "data_contract": _c("data contract", "dtaa contract"),
        "nr_contract": _c("numar contract"),
        "denumire": _c("denumire contract"),
        "valoare_ron": _c(
            "valoare atribuita ron", "valoare contract ron", "valoare atribuita"
        ),
        "furnizor": _c("ofertant castigator", "nume castigator", "ofertant"),
        "furnizor_cui": _c(
            "cui ofertant castigator", "cui castigator", "cui ofertant"
        ),
    },
)

INITIERE = TableSpec(
    key="initiere",
    # The initiation notice has been published under several names:
    #   2017-2018  "Anunturi participare"
    #   2020-2021  "Anunțuri inițiere"            (no "de")
    #   2022-2026  "Anunturi de initiere publicate", "... publicate in SEAP"
    # Requiring "anunturi de initiere" silently lost this table for four years — and it
    # is the one the estimate-versus-award comparison depends on.
    #
    # Anchored on "anunturi" so "Invitatii participare" (invitations to an existing
    # dynamic purchasing system, a different record) does not match.
    match=re.compile(r"(?i)\banun[tț]uri\s+(de\s+)?(ini[tț]iere|participare)\b"),
    columns={
        # The 2016-2018 exports abbreviate: DenumireAC / CUI / MainCPV / ValoareEstimata.
        # Only three of fourteen columns matched before these aliases, so the table was
        # present but almost entirely empty.
        "autoritate": _c(
            "autoritate contractanta", "denumireac",
            "denumire autoritate contractanta",  # 2016
        ),
        "autoritate_cui": _c(
            "cui autoritate contractanta", "cui", "cui ac", "cui autoritate"
        ),
        "tip_anunt": _c("tip anunt", "tip"),
        "tip_procedura": _c("tip procedura", "tipprocedura"),
        "stare_procedura": _c("stare procedura"),
        "nr_anunt_initiere": _c(
            "numar anunt initiere", "numaranunt", "numar anunt invitatie"
        ),
        "data_publicare": _c("data publicare", "datapublicare"),
        "tip_contract": _c("tip contract", "tipcontract"),
        "criteriu_atribuire": _c("criteriuatribuire", "criteriu de atribuire"),
        "modalitate_atribuire": _c(
            "modalitate de atribuire", "modalitatedesfasurare", "modalitate atribuire"
        ),
        "loturi": _c("contractul este impartit in loturi", "cu loturi"),
        "denumire": _c("denumire procedura"),
        "cpv": _c("cod cpv", "maincpv", "main cpv code", "cod cpv procedura"),
        "cpv_denumire": _c(
            "denumire cpv", "maincpvname", "denumire cod cpv", "denumire cpv procedura"
        ),
        "valoare_estimata_ron": _c(
            "valoare estimata procedura ron", "valoareestimata",
            "valoare estimata procedura",
        ),
        # County, present ONLY in the 2016-2018 exports. The modern ones dropped it,
        # which is why county otherwise has to come from the SEAP entity endpoint.
        "judet": _c("judet", "judet autoritate"),
        "utilitati": _c("utilitati"),
        "fonduri_comunitare": _c("fonduricomunitare", "finantare prin fonduri comunitare"),
    },
)

MODIFICARI = TableSpec(
    key="modificari",
    match=re.compile(r"(?i)modificare\s+contract"),
    columns={
        "autoritate": _c("autoritate contractanta", "denumire autoritate contractanta"),
        "autoritate_cui": _c("cui autoritate contractanta"),
        "nr_anunt_atribuire": _c("numar anunt atribuire"),
        "data_publicare": _c("data publicare", "data anunt de modificare"),
        "nr_contract": _c("numar contract"),
        "data_contract": _c("data contract"),
        "descriere_modificari": _c(
            "descrierea modificarilor", "secunea vii 2 1 descrierea modificarilor",
            "sectiunea vii 2 1 descrierea modificarilor",
        ),
        "valoare_inainte_ron": _c(
            "valoarea totala actualizata a contractului inainte de modificari"
        ),
        "valoare_dupa_ron": _c("valoarea totala a contractului dupa modificari"),
    },
)

TABLES = (ACHIZITII_DIRECTE, CONTRACTE, FARA_ANUNT, INITIERE, MODIFICARI)
TABLES_BY_KEY = {t.key: t for t in TABLES}


# Columns that are legitimately empty in some years. Without this, the validation
# harness reports genuine publishing gaps as defects — and a checker that cries wolf
# gets ignored, which defeats the point of having one.
#
# Every entry needs a reason. "It looked fine" is not one: each of these was confirmed
# against the source header before being listed — the column is genuinely absent, not
# merely unmatched. That distinction has been got wrong in both directions here:
# `subcontractat` looked absent but was a reporting-convention change, and `initiere`
# looked present but was three mapped columns out of eighteen.
#
# A column may appear more than once with different periods and different reasons, so
# each key holds a tuple of entries.
KNOWN_COLUMN_GAPS: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {
    ("contracte", "tip_contract"): (
        {
            "years": (2022,),
            "reason": (
                "The 2022 Q1 export publishes nineteen columns and omits contract type, "
                "CPV, award criteria and several others; the other three quarters carry "
                "them. The gap is partial — about 41% of that year's rows — so a "
                "year-level statement would overstate it in both directions."
            ),
        },
    ),
    ("contracte", "cpv"): (
        {
            "years": (2022,),
            "reason": (
                "Absent from the 2022 Q1 export, which carries NUTS region codes where "
                "the other quarters carry CPV. Roughly 41% of that year's contracts "
                "therefore cannot be classified by procurement category at all."
            ),
        },
    ),
    ("contracte", "tip_incheiere"): (
        {
            "years": (2022,),
            "reason": (
                "Absent from the 2022 Q1 export. Without it a framework agreement "
                "cannot be told from an ordinary contract for those rows, so the "
                "double-counting guard cannot be applied to them and their values must "
                "not be summed with the rest."
            ),
        },
    ),
    ("contracte", "criteriu_atribuire"): (
        {
            "years": (2022,),
            "reason": (
                "Omitted by the 2022 Q1 export while the other quarters carry it, so "
                "award-criteria analysis covers roughly three fifths of that year."
            ),
        },
    ),
    ("achizitii_directe", "cpv_denumire"): (
        {
            "years": (2021,),
            "reason": (
                "The 2021 snake-case exports carry CPV_CODE_ID, a numeric internal "
                "identifier, rather than a CPV label — which is why that column is "
                "deliberately not mapped here. The code itself is present, so the label "
                "is recoverable from the EU vocabulary."
            ),
        },
    ),
    ("contracte", "numar_oferte"): (
        {
            # 2022 Q4 publishes it as "Numar oferte" and DOES carry it (335,080 rows),
            # so the gap is not continuous. 2018 and 2022 are partial: some quarters of
            # those years carry the column and others do not.
            "years": (2018, 2019, 2020, 2021, 2022),
            "reason": (
                "The publisher stopped including NumarOfertePrimite after 2018. This is a "
                "real loss of transparency, not a mapping failure: single-bidder rate is "
                "the strongest indicator in the literature and cannot be computed for "
                "these years from the bulk exports at all."
            ),
        },
        {
            "years": range(2023, 2027),
            "reason": (
                "Absent again from 2023 onward. The column reappeared briefly in 2022 "
                "Q4 under the name 'Numar oferte', which is why the gap is recorded as "
                "two windows rather than one continuous run."
            ),
        },
    ),
    ("contracte", "subcontractat"): (
        {
            # 2022 is partial: Q1 omits the column, the other quarters carry it.
            "years": (2022, *range(2023, 2027)),
            "reason": (
                "Dropped from the contracts export after 2022. Subcontracting cannot be "
                "measured from the bulk data for recent years at all."
            ),
        },
        {
            "years": (2016, 2017, 2018),
            "reason": (
                "Reporting convention change, not absence. 2016-2017 record only 'DA' and "
                "leave the rest blank; 2019 onward record 'DA' and 'NU' explicitly. A "
                "blank in the early years therefore means 'not subcontracted' — reading "
                "it as missing would understate subcontracting."
            ),
        },
    ),
    ("contracte", "valoare_estimata_ron"): (
        {
            "years": range(2023, 2027),
            "reason": (
                "The estimate stops being carried on the contract row from 2023. An "
                "earlier note here recorded 2022 as 40.6% null and reasoned about why "
                "the source might have dropped it mid-year; that was wrong. The "
                "nulls were ours — one quarter heads the column VALOARE_ESTIMATA_RON "
                "and it was not mapped. With the alias added, 2022 is 99.7% populated "
                "and estimare-01 rises from 24,543 findings to 42,631. From 2023 the "
                "column really is absent, and the estimate must come from the "
                "initiation notice, which reintroduces the framework double-counting "
                "problem."
            ),
        },
    ),
    ("contracte", "licitatie_electronica"): (
        {
            "years": range(2019, 2027),
            "reason": (
                "CuLicitatieElectronica appears only in the 2016-2018 exports."
            ),
        },
        {
            # A different kind of gap from the one above, and worth separating: in these
            # years the column IS mapped, it is simply mostly empty — 78% in 2016, 92%
            # in 2017, 42% in 2018. It records whether an electronic auction was held,
            # which is optional, so a blank most likely means "no auction" rather than
            # "not recorded". The source does not distinguish the two, and neither
            # reading is asserted here.
            "years": (2016, 2017, 2018),
            "reason": (
                "Present but sparsely filled, and an empty cell is indistinguishable "
                "from a recorded 'no'. The column therefore supports a lower bound on "
                "electronic auctions and nothing stronger."
            ),
        },
    ),
    ("contracte", "cpv_denumire"): (
        {
            "years": range(2016, 2023),
            "reason": (
                "Only the CPV code is published before 2023, not its label. The label is "
                "recoverable from the code via the EU vocabulary, so nothing is lost."
            ),
        },
    ),
    ("contracte", "nr_lot"): (
        {
            # 2022 included: lot numbering arrived mid-year, so one quarter still lacks
            # it and the year is 59% null rather than 0% or 100%.
            "years": range(2016, 2023),
            "reason": (
                "Lot numbering was introduced in the 2022 export. Before that a multi-lot "
                "award appears as several rows with no lot identifier, so lots cannot "
                "be distinguished from separate contracts."
            ),
        },
    ),
    ("contracte", "incheiat_prin"): (
        {
            # 2022 and 2024 are partial — one quarter of each omits the flag.
            "years": (*range(2016, 2022), 2022, 2024),
            "reason": (
                "The framework call-off flag was introduced in 2022. For earlier years "
                "tip_incheiere still separates an acord-cadru from an ordinary contract, "
                "which is what the double-counting guard relies on."
            ),
        },
    ),
    ("achizitii_directe", "tip_procedura"): (
        {
            "years": range(2022, 2027),
            "reason": (
                "Dropped from the modern direct-acquisition exports. It carried no "
                "information in any case: every row in this table IS a direct "
                "acquisition, so the column was constant."
            ),
        },
    ),
    ("achizitii_directe", "furnizor_localitate"): (
        {
            "years": (2022, 2023, 2024, 2025, 2026),
            "reason": (
                "Supplier locality appears intermittently — fully present 2016-2021, "
                "then from 2022 only in some quarters of a year rather than none or all "
                "(2023 is 77% null, not 100%). Where absent it can only come from ONRC "
                "or the SEAP entity endpoint."
            ),
        },
    ),
    ("achizitii_directe", "tip_contract"): (
        {
            # 2024 is partial: one quarter drops the column, the others keep it.
            "years": (2022, 2024),
            "reason": (
                "The 2022 export publishes thirteen columns and omits contract type "
                "entirely. Category is derived from the CPV division instead, which "
                "resolves for 99.99% of that year's rows."
            ),
        },
    ),
    ("initiere", "tip_anunt"): (
        {
            "years": (2016, 2017, 2018),
            "reason": (
                "The 2016-2018 initiation exports carry no notice-type column; TIP_ANUNT "
                "first appears in the 2019 snake-case format. Every row in those files "
                "is a participation notice, so the column carried no information."
            ),
        },
    ),
    ("initiere", "stare_procedura"): (
        {
            "years": range(2016, 2027),
            "reason": (
                "Procedure status is not published in any bulk initiation export examined "
                "(2016-2026); it exists only in the SEAP API."
            ),
        },
    ),
    ("initiere", "loturi"): (
        {
            "years": range(2016, 2027),
            "reason": (
                "The lot-split flag is not published in the bulk initiation exports; only "
                "the contracts table carries lot numbers."
            ),
        },
    ),
    ("initiere", "denumire"): (
        {
            "years": range(2016, 2023),
            "reason": (
                "The procedure title is not published in the initiation exports before "
                "2023. Those years support counting and value analysis but not text "
                "matching against the object of the procurement."
            ),
        },
    ),
    ("initiere", "criteriu_atribuire"): (
        {
            "years": range(2023, 2027),
            "reason": (
                "Award criteria were dropped from the initiation export after 2022. They "
                "remain on the contracts table, so criterion analysis must be done "
                "there for recent years."
            ),
        },
    ),
    ("initiere", "utilitati"): (
        {
            "years": range(2023, 2027),
            "reason": (
                "The utilities-sector flag was dropped from the initiation export after "
                "2022."
            ),
        },
    ),
    ("initiere", "fonduri_comunitare"): (
        {
            "years": range(2023, 2027),
            "reason": (
                "The EU-funding flag was dropped from the initiation export after 2022, so "
                "EU-funded procurement cannot be isolated from this table for recent "
                "years."
            ),
        },
    ),
    ("initiere", "judet"): (
        {
            "years": (2025, 2026),
            "reason": (
                "County reappeared in the 2023-2024 initiation exports and was dropped "
                "again for 2025-2026. Where absent it must come from the SEAP entity "
                "endpoint, one authority at a time."
            ),
        },
    ),
}


def is_known_gap(table: str, column: str, year: int) -> dict[str, Any] | None:
    """Return the documented reason a column is empty that year, or None."""
    for entry in KNOWN_COLUMN_GAPS.get((table, column), ()):
        if year in entry["years"]:
            return entry
    return None


# --------------------------------------------------------------------- format sniff

def sniff(blob: bytes) -> str:
    """Identify a payload from its magic bytes.

    The `format` field on data.gov.ro is unreliable: 2026 Q2 serves OpenDocument files
    under `.xlsx` names and declares them as `.xlsx`. Content wins over metadata.
    """
    if blob[:4] == b"PK\x03\x04":
        try:
            names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
        except zipfile.BadZipFile:
            return "unknown"
        if "content.xml" in names or any(n.startswith("META-INF/") for n in names):
            return "ods"
        if "[Content_Types].xml" in names or any(n.startswith("xl/") for n in names):
            return "xlsx"
        return "unknown"
    if blob[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"          # OLE2 compound document
    sample = blob[:4096].lstrip(b"\xef\xbb\xbf")
    if sample.startswith((b"<?xml", b"<")):
        return "xml"
    return "csv"


def _sniff_delimiter(text: str) -> str:
    """Delimiters vary by year and are never declared.

    2016-2017 exports are caret-delimited, 2023 Q3 is PIPE-delimited, others use comma
    or semicolon. Omitting the pipe made a 142 MB file parse as a single column: the
    header matched no aliases, so all 529,483 rows were dropped without an error.
    """
    return _sniff_dialect(text)[0]


def _sniff_dialect(text: str) -> tuple[str, str]:
    """Delimiter and quote character. Neither is ever declared.

    2022 wraps every field in pipes and separates with commas:

        |DA31518890|,|09/30/2022|,|Oferta acceptata|,|SCOALA GIMNAZIALA ...|

    Counting raw occurrences picks the pipe there — two per field against one comma —
    and shatters each row into fragments. The header then matches almost nothing, which
    left 1.8M rows of 2022 with every major column NULL.
    """
    head = text.split("\n", 1)[0]
    if head.lstrip("﻿").startswith("|") and "|,|" in head:
        return ",", "|"
    return max("|^;,\t", key=head.count), '"'


# ------------------------------------------------------------------------- readers

def _rows_csv(blob: bytes) -> tuple[list[str], list[list[str]]]:
    for enc in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            text = blob.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = blob.decode("utf-8", errors="replace")
    delimiter, quotechar = _sniff_dialect(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, quotechar=quotechar)
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _merge_sheet(
    header: list[str], rows: list[list[str]]
) -> tuple[list[str], list[list[str]]]:
    """Fold one sheet into an accumulating table.

    The legacy .xls format caps a SHEET at 65,536 rows, so large exports are split
    across many. The 2023 Q1 direct-acquisition file holds 584,138 rows across eleven
    sheets named "Sheet 1", "Sheet 2", "Sheet 4" and so on — non-contiguous, so every
    sheet must be visited. Reading only the first silently discarded 519,137 rows.

    Each continuation sheet repeats the header, which is dropped when it matches.
    """
    if not rows:
        return header, []
    if not header:
        return rows[0], rows[1:]
    return header, (rows[1:] if rows[0] == header else rows)


def _rows_xlsx(blob: bytes) -> tuple[list[str], list[list[str]]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    header: list[str] = []
    out: list[list[str]] = []
    for ws in wb.worksheets:
        # Read-only mode trusts the sheet's declared dimension, and several exports
        # declare a false one: the 2019-2020 direct-acquisition files claim max_row=1,
        # max_col=1 despite holding 500,000+ rows. reset_dimensions() derives the real
        # extent from the data while still streaming.
        ws.reset_dimensions()
        rows = [["" if v is None else str(v) for v in row]
                for row in ws.iter_rows(values_only=True)]
        header, chunk = _merge_sheet(header, rows)
        out.extend(chunk)
    wb.close()
    return header, out


def _rows_xls(blob: bytes) -> tuple[list[str], list[list[str]]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=blob)
    header: list[str] = []
    out: list[list[str]] = []
    for index in range(book.nsheets):
        sheet = book.sheet_by_index(index)
        rows = [[str(c) for c in sheet.row_values(i)] for i in range(sheet.nrows)]
        header, chunk = _merge_sheet(header, rows)
        out.extend(chunk)
    return header, out


_ODS_NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
_T = f"{{{_ODS_NS['table']}}}"
_O = f"{{{_ODS_NS['office']}}}"
_TX = f"{{{_ODS_NS['text']}}}"

# A trailing cell can declare a repeat count in the millions to pad the sheet. Expanding
# that literally exhausts memory, so repeats are capped.
_MAX_REPEAT = 256


def _rows_ods(blob: bytes) -> tuple[list[str], list[list[str]]]:
    """Stream an OpenDocument spreadsheet.

    A DOM parser (odfpy) is unusable here: the 2026 Q2 direct-acquisitions export is
    ~100 MB compressed and decompresses to far more XML than fits comfortably in memory.
    `iterparse` keeps memory bounded and is an order of magnitude faster, at the cost of
    handling the repeat/blank semantics by hand.
    """
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(io.BytesIO(blob)) as z, z.open("content.xml") as fh:
        out: list[list[str]] = []
        row: list[str] = []
        cell_text: list[str] = []
        cell_repeat = 1
        cell_value: str | None = None
        in_cell = False

        for event, elem in ET.iterparse(fh, events=("start", "end")):
            tag = elem.tag
            if event == "start":
                if tag == f"{_T}table-cell":
                    in_cell = True
                    cell_text = []
                    cell_value = elem.get(f"{_O}value") or elem.get(f"{_O}date-value")
                    cell_repeat = min(
                        int(elem.get(f"{_T}number-columns-repeated") or 1), _MAX_REPEAT
                    )
                elif tag == f"{_T}table-row":
                    row = []
                continue

            # end events
            if tag == f"{_TX}p" and in_cell:
                cell_text.append("".join(elem.itertext()))
            elif tag == f"{_T}table-cell":
                value = cell_value if cell_value is not None else "\n".join(cell_text)
                row.extend([value] * cell_repeat)
                in_cell = False
                elem.clear()
            elif tag == f"{_T}table-row":
                while row and row[-1] == "":
                    row.pop()
                out.append(row)
                elem.clear()
            elif tag == f"{_T}table":
                break  # first sheet only

    while out and not any(out[-1]):
        out.pop()
    if not out:
        return [], []
    return out[0], out[1:]


READERS = {"csv": _rows_csv, "xlsx": _rows_xlsx, "xls": _rows_xls, "ods": _rows_ods}


def read_table(blob: bytes) -> tuple[list[str], list[list[str]]]:
    kind = sniff(blob)
    reader = READERS.get(kind)
    if reader is None:
        raise ValueError(f"unsupported payload format: {kind}")
    return reader(blob)


# ------------------------------------------------------------------------- mapping

_PUNCT = re.compile(r"[^\w\s]")


def _header_key(name: str) -> str:
    """Normalise a source header for alias matching.

    Collapses to letters and digits only, because the exports use at least three
    conventions for the same column:

        "CUI autoritate contractanta"   spaced      (2022-2026)
        "AutoritateContractantaCUI"     camel       (2016-2018 CSV)
        "AUTORITATE_CONTRACTANTA_CUI"   snake caps  (2019-2021)

    Underscores are word characters, so a fold that only stripped punctuation left
    `castigator_cui` unmatched against the alias `castigatorcui`. The 2021 export
    silently lost autoritate_cui, furnizor_cui, cpv and tip_contract that way — 100%
    NULL across 1,043,345 rows, with no error.
    """
    return re.sub(r"[^a-z0-9]", "", fold(name))


MIN_PREFIX_ALIAS = 24
"""Only aliases at least this long may match as a prefix."""


def map_columns(header: list[str], spec: TableSpec) -> dict[str, int]:
    """canonical name -> column index, for the columns this file actually has.

    Exact match first. Failing that, a LONG alias may match a header that merely starts
    with it, because the form-style exports carry explanatory text inside the column
    name itself:

        "Valoarea totala actualizata a contractului inainte de modificari (luand in
         considerare eventualele modificari ale contractului si adaptarii ale
         preturilor...."

    Exact matching can never hit that, and chasing each variant by hand is futile — the
    trailing prose differs between quarters. The length floor keeps short aliases such
    as "cui" or "valoare" from matching half the header by accident.
    """
    lookup = {_header_key(h): i for i, h in enumerate(header) if h}
    mapping: dict[str, int] = {}
    for canonical, aliases in spec.columns.items():
        for alias in aliases:
            key = _header_key(alias)
            idx = lookup.get(key)
            if idx is None and len(key) >= MIN_PREFIX_ALIAS:
                idx = next(
                    (i for h, i in lookup.items() if h.startswith(key)), None
                )
            if idx is not None:
                mapping[canonical] = idx
                break
    return mapping


def missing_columns(header: list[str], spec: TableSpec) -> list[str]:
    """Canonical fields this file has no source column for.

    The inverse of `unmapped headers`, and the more dangerous direction: an unmatched
    alias produces a column of NULLs rather than an error. The 2021 export lost four
    columns this way without anything failing.
    """
    return sorted(set(spec.columns) - set(map_columns(header, spec)))


MAX_PREAMBLE_SCAN = 12
"""Rows to examine when looking for the real header."""


def _overlay(primary: list[str], secondary: list[str]) -> list[str]:
    """Combine two header rows, preferring a non-empty cell from `primary`."""
    width = max(len(primary), len(secondary))
    return [
        (primary[i].strip() if i < len(primary) else "")
        or (secondary[i].strip() if i < len(secondary) else "")
        for i in range(width)
    ]


def realign_header(
    header: list[str], rows: list[list[str]], spec: TableSpec
) -> tuple[list[str], list[list[str]]]:
    """Find the real header, which is not always row zero.

    Three layouts occur in these exports:

    1. Row zero is the header — the ordinary case, left untouched.
    2. Row zero is a title banner ("Raport Achizitii directe Trimestrul I 2023") and the
       header sits below it. Treating the banner as the header maps nothing, so every
       record becomes all-NULL and is dropped: a 167 MB file parsed to zero rows.
    3. The header spans TWO rows with merged cells. The contract-modification exports
       put a section number on one row ("VII.2.3 Creșterea prețului") and the column name
       on the next ("Valoarea totala actualizata a contractului inainte de modificari"),
       each covering columns the other leaves blank. Neither row alone maps everything,
       so both overlay orders are scored — which order wins depends on the file.

    Candidates are scored against the alias table and the best wins.
    """
    def score(candidate: list[str]) -> int:
        return len(map_columns(candidate, spec))

    best_score = score(header)
    best: tuple[list[str], int] | None = None  # (header, rows consumed)

    # The two-row case can begin at row zero itself: the contract-modification exports
    # put section numbers in the header row and the column names in the row below it.
    if rows:
        for candidate in (_overlay(header, rows[0]), _overlay(rows[0], header)):
            if (s := score(candidate)) > best_score:
                best_score, best = s, (candidate, 1)

    for i, row in enumerate(rows[:MAX_PREAMBLE_SCAN]):
        candidates: list[tuple[list[str], int]] = [(row, 1)]
        if i + 1 < len(rows):
            candidates.append((_overlay(row, rows[i + 1]), 2))
            candidates.append((_overlay(rows[i + 1], row), 2))
        for candidate, consumed in candidates:
            if (s := score(candidate)) > best_score:
                best_score, best = s, (candidate, i + consumed)

    if best is None:
        return header, rows
    candidate, consumed = best
    log.info(
        "header resolved after %d row(s) (%d columns matched, vs %d on row 0)",
        consumed, best_score, score(header),
    )
    return candidate, rows[consumed:]


def is_repeated_header(rec: dict[str, Any], spec: TableSpec) -> bool:
    """True when a data row is actually a copy of the header.

    Quarterly exports are sometimes concatenated with their header line intact, so the
    header arrives again as data. Nineteen such rows reached the published site as a
    contracting authority literally named "Autoritate contractanta", with the CUI "CUI
    autoritate contractanta" and 19 acquisitions to its name.

    The test requires at least TWO fields to equal one of the accepted source headers
    for that same field. One match could be a coincidence — an organisation really can
    be called something unfortunate — but two independent columns each containing their
    own column name is a header row, not a record.
    """
    matches = 0
    for canonical, aliases in spec.columns.items():
        value = rec.get(canonical)
        if not value:
            continue
        if fold(str(value)) in {fold(a) for a in aliases}:
            matches += 1
            if matches >= 2:
                return True
    return False


def to_records(
    header: list[str], rows: list[list[str]], spec: TableSpec, source: str
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Map rows onto the canonical schema.

    Returns (records, unmapped_headers, malformed_row_count).

    Rows with MORE fields than the header are dropped. The 2016-2018 exports are
    caret-delimited and unquoted, so a description containing "^" splits into extra
    fields and shifts every column after it — product text then lands in `tip_contract`,
    CUIs in `cpv`, and so on. Such a row cannot be realigned reliably, and keeping it
    injects garbage into columns that look populated. Roughly 132 rows in 854,898 for
    2017 T1 (0.015%).

    Rows with FEWER fields are kept: trailing empty fields are routinely omitted and
    the missing values simply become None.
    """
    mapping = map_columns(header, spec)
    unmapped = [
        h for i, h in enumerate(header) if h and i not in set(mapping.values())
    ]
    width = len(header)
    records: list[dict[str, Any]] = []
    malformed = 0
    repeated_headers = 0
    for row in rows:
        if len(row) > width:
            malformed += 1
            continue
        rec: dict[str, Any] = {c: None for c in spec.columns}
        for canonical, idx in mapping.items():
            if idx < len(row):
                value = (row[idx] or "").strip()
                rec[canonical] = value or None
        if not any(rec.values()):
            continue
        if is_repeated_header(rec, spec):
            repeated_headers += 1
            continue
        rec["sursa"] = source
        records.append(rec)
    if repeated_headers:
        log.info("%s: dropped %d repeated header rows", source, repeated_headers)
    return records, unmapped, malformed


# ----------------------------------------------------------------------- discovery

# The same quarter is frequently published more than once — as a .csv and a .xls of
# identical data, or as two resources with the same name. Ingesting both would double
# every row for that quarter, which for a project about public money is the worst kind
# of error: silent inflation of spending.
#
# Formats are ranked by how reliably they have parsed in practice. CSV has no sheet
# limits and no declared-dimension to lie about; XLS is last because it caps a sheet at
# 65,536 rows, forcing multi-sheet handling, and its exports carry title banners.
_FORMAT_RANK = {"csv": 0, "xlsx": 1, "ods": 2, "xls": 3}
_QUARTER = re.compile(r"(?i)\bT\s*\.?\s*(IV|III|II|I|[1-4])\b")
_SEMESTER = re.compile(r"(?i)\bS\s*\.?\s*([12])\b")
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


@dataclass(frozen=True)
class Resource:
    year: int
    table: TableSpec
    name: str
    url: str

    @property
    def period(self) -> str:
        """Quarter or semester this resource covers, for de-duplication."""
        if m := _QUARTER.search(self.name):
            token = m.group(1).upper()
            return f"T{_ROMAN.get(token, token)}"
        if m := _SEMESTER.search(self.name):
            return f"S{m.group(1)}"
        return "?"

    @property
    def format_rank(self) -> int:
        """Lower is preferred. Derived from the URL, since declared formats lie."""
        suffix = self.url.rsplit(".", 1)[-1].lower()
        if suffix not in _FORMAT_RANK:
            suffix = self.name.rsplit(".", 1)[-1].lower()
        return _FORMAT_RANK.get(suffix, 9)

    @property
    def slug(self) -> str:
        safe = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return f"{self.year}-{self.table.key}-{safe}"[:120]


DISCOVER_RETRIES = 4


def _get_with_retry(
    client: httpx.Client, url: str, *, params: dict[str, Any] | None = None, **kw: Any
) -> httpx.Response:
    """GET with backoff on transient failures.

    data.gov.ro intermittently refuses or stalls connections — a bare ConnectTimeout on
    the very first discovery call killed a whole backfill in CI. Transport-level retries
    do not cover read timeouts or 5xx, so both are retried here.
    """
    last: Exception | None = None
    for attempt in range(DISCOVER_RETRIES):
        try:
            r = client.get(url, params=params, **kw)
        except httpx.HTTPError as exc:
            last = exc
            log.warning("GET %s failed (%s), attempt %d", url, exc, attempt + 1)
        else:
            if r.status_code < 500:
                return r
            last = httpx.HTTPStatusError(
                f"HTTP {r.status_code}", request=r.request, response=r
            )
            log.warning("GET %s -> %d, attempt %d", url, r.status_code, attempt + 1)
        time.sleep(2**attempt)
    assert last is not None
    raise last


def discover(year: int, client: httpx.Client) -> list[Resource]:
    """Resources for one year, classified by logical table.

    Resources whose name matches no known table (SAD invitations, award notifications)
    are skipped and logged rather than silently dropped.
    """
    payload = None
    for template in DATASET_SLUGS:
        slug = template.format(year=year)
        r = _get_with_retry(client, CKAN, params={"id": slug})
        if r.status_code == 404:
            continue
        r.raise_for_status()
        candidate = r.json()
        if candidate.get("success"):
            if template is not DATASET_SLUGS[0]:
                log.info("%d: found under fallback slug %r", year, slug)
            payload = candidate
            break

    if payload is None:
        log.warning("no dataset for %d (tried %s)",
                    year, [t.format(year=year) for t in DATASET_SLUGS])
        return []

    found: list[Resource] = []
    skipped: list[str] = []
    for res in payload["result"]["resources"]:
        name = (res.get("name") or "").strip()
        url = res.get("url")
        if not url:
            continue
        for spec in TABLES:
            if spec.match.search(name):
                found.append(Resource(year, spec, name, url))
                break
        else:
            skipped.append(name)
    if skipped:
        log.info("%d: skipped %d unclassified resources, e.g. %s",
                 year, len(skipped), skipped[:2])
    return _dedupe_periods(found, year)


def _dedupe_periods(found: list[Resource], year: int) -> list[Resource]:
    """Keep one resource per (table, period), preferring the more reliable format."""
    best: dict[tuple[str, str], Resource] = {}
    dropped: list[str] = []
    for res in found:
        key = (res.table.key, res.period)
        current = best.get(key)
        if current is None:
            best[key] = res
        elif res.format_rank < current.format_rank:
            best[key] = res
            dropped.append(current.name)
        else:
            dropped.append(res.name)
    if dropped:
        log.info("%d: dropped %d duplicate resources, e.g. %s",
                 year, len(dropped), dropped[:2])
    # A resource whose period cannot be parsed is never de-duplicated away: guessing
    # would risk discarding a quarter entirely.
    unknown = [r for r in found if r.period == "?"]
    return sorted(
        {id(r): r for r in [*best.values(), *unknown]}.values(),
        key=lambda r: (r.table.key, r.period, r.name),
    )


def fetch(resource: Resource, cache_dir: Path, client: httpx.Client) -> bytes:
    """Download with an on-disk cache; these files are large and immutable once published."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{resource.slug}.bin"
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    log.info("downloading %s", resource.name)
    r = _get_with_retry(client, resource.url, follow_redirects=True, timeout=600.0)
    r.raise_for_status()
    path.write_bytes(r.content)
    return r.content


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": f"achizitii-deschise/0.1 (+{CONTACT})"},
        # Connect generously: data.gov.ro is slow to accept connections from cloud
        # runners. Read timeout is large because single exports exceed 100 MB.
        timeout=httpx.Timeout(connect=60.0, read=600.0, write=60.0, pool=60.0),
        transport=httpx.HTTPTransport(retries=3),
        follow_redirects=True,
    )
