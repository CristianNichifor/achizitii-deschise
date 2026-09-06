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
        "autoritate": _c("autoritate contractanta", "autoritatecontractanta"),
        "autoritate_cui": _c("cui autoritate contractanta", "autoritatecontractantacui"),
        "nr_achizitie": _c("numar achizitie directa", "numaranunt", "numar anunt"),
        "data_publicare": _c("data publicare", "dataanunt", "data anunt"),
        "data_finalizare": _c("data finalizare", "datacontract", "data contract"),
        "denumire": _c("denumire achizitie", "descriere", "titlucontract"),
        "cpv": _c("cod cpv", "cpvcode"),
        # NOT "cpvcodeid": that column holds a numeric internal id (39831240 -> 15113),
        # not a label. Mapping it here filled the field with meaningless integers.
        "cpv_denumire": _c("denumire cpv", "cpvcodename", "denumire cod cpv"),
        "tip_contract": _c("tip contract", "tipincheierecontract"),
        "valoare_ron": _c("valoare achizitie ron", "valoareron", "valoare"),
        "furnizor": _c("ofertant castigator", "castigator"),
        "furnizor_cui": _c("cui ofertant castigator", "castigatorcui"),
        # present only in the older exports
        "furnizor_localitate": _c("castigatorlocalitate"),
        "tip_procedura": _c("tip procedura", "tipprocedura"),
    },
)

CONTRACTE = TableSpec(
    key="contracte",
    match=re.compile(r"(?i)^(?!.*subsecvent).*\bcontracte\b(?!.*modificare)"),
    columns={
        "autoritate": _c("autoritate contractanta", "autoritatecontractanta"),
        "autoritate_cui": _c("cui autoritate contractanta", "autoritatecontractantacui"),
        "tip_procedura": _c("tip procedura", "tipprocedura"),
        "nr_anunt_initiere": _c("numar anunt initiere", "numaranuntparticipare"),
        "nr_anunt_atribuire": _c("numar anunt atribuire", "numaranuntatribuire", "numaranunt"),
        "data_publicare": _c("data publicare", "dataanuntatribuire", "dataanunt"),
        "tip_contract": _c("tip contract", "tipcontract"),
        "criteriu_atribuire": _c(
            "tip criterii de atribuire", "criteriu de atribuire", "tipcriteriiatribuire"
        ),
        "cpv": _c("cod cpv", "cpvcode"),
        "cpv_denumire": _c("denumire cpv", "cpvcodename"),
        "nr_lot": _c("numar lot"),
        # WITHOUT THESE TWO, CONTRACT VALUES CANNOT BE SUMMED SAFELY.
        # 95% of contract rows are framework agreements (217,407 of 229,089 in H1 2026),
        # averaging 55.6 contracts per notice, and 128,920 of those are call-offs under
        # a framework whose headline value is also present. Adding them together
        # double-counts massively: 534.4 bn RON of "acord-cadru" against 176.9 bn of
        # actual public procurement contracts.
        "tip_incheiere": _c("tip incheiere contract", "tipincheierecontract"),
        "incheiat_prin": _c("incheiat prin"),
        # Number of offers received. Present only in the 2016-2018 era exports and
        # dropped from later ones — which is unfortunate, because single-bidder rate is
        # the strongest indicator in the procurement-corruption literature. Its absence
        # from the modern exports is why that indicator is limited to the early years.
        "numar_oferte": _c("numaroferteprimite", "numar oferte primite"),
        # The early exports carry the estimate on the contract row itself, so
        # estimate-versus-award needs no join for those years.
        "valoare_estimata_ron": _c("valoareestimataparticipare", "valoare estimata"),
        "licitatie_electronica": _c("culicitatieelectronica"),
        "subcontractat": _c("subcontractat"),
        "data_contract": _c("data contract", "datacontract"),
        "nr_contract": _c("numar contract", "numarcontract"),
        "valoare_ron": _c("valoare contract ron", "valoareron", "valoare"),
        "furnizor": _c("ofertant castigator", "castigator"),
        "furnizor_cui": _c("cui ofertant castigator", "castigatorcui"),
    },
)

FARA_ANUNT = TableSpec(
    key="fara_anunt",
    match=re.compile(r"(?i)f[aă]r[aă]\s+anun[tț]\s+de\s+ini[tț]iere"),
    columns={
        "autoritate": _c("autoritate contractanta"),
        "autoritate_cui": _c("cui autoritate contractanta"),
        "tip_procedura": _c("tip procedura"),
        "nr_anunt_atribuire": _c("numar anunt atribuire"),
        "data_publicare": _c("data publicare"),
        "tip_contract": _c("tip contract"),
        "criteriu_atribuire": _c("criteriu de atribuire", "tip criterii de atribuire"),
        "cpv": _c("cod cpv"),
        "cpv_denumire": _c("denumire cpv"),
        "data_contract": _c("data contract"),
        "nr_contract": _c("numar contract"),
        "denumire": _c("denumire contract"),
        "valoare_ron": _c("valoare atribuita ron", "valoare contract ron"),
        "furnizor": _c("ofertant castigator"),
        "furnizor_cui": _c("cui ofertant castigator"),
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
        "autoritate_cui": _c("cui autoritate contractanta", "cui", "cui ac"),
        "tip_anunt": _c("tip anunt", "tip"),
        "tip_procedura": _c("tip procedura", "tipprocedura"),
        "stare_procedura": _c("stare procedura"),
        "nr_anunt_initiere": _c(
            "numar anunt initiere", "numaranunt", "numar anunt invitatie"
        ),
        "data_publicare": _c("data publicare", "datapublicare"),
        "tip_contract": _c("tip contract", "tipcontract"),
        "criteriu_atribuire": _c("criteriuatribuire", "criteriu de atribuire"),
        "modalitate_atribuire": _c("modalitate de atribuire", "modalitatedesfasurare"),
        "loturi": _c("contractul este impartit in loturi"),
        "denumire": _c("denumire procedura"),
        "cpv": _c("cod cpv", "maincpv", "main cpv code"),
        "cpv_denumire": _c("denumire cpv", "maincpvname", "denumire cod cpv"),  # 2016
        "valoare_estimata_ron": _c("valoare estimata procedura ron", "valoareestimata"),
        # County, present ONLY in the 2016-2018 exports. The modern ones dropped it,
        # which is why county otherwise has to come from the SEAP entity endpoint.
        "judet": _c("judet"),
        "utilitati": _c("utilitati"),
        "fonduri_comunitare": _c("fonduricomunitare", "finantare prin fonduri comunitare"),
    },
)

MODIFICARI = TableSpec(
    key="modificari",
    match=re.compile(r"(?i)modificare\s+contract"),
    columns={
        "autoritate": _c("autoritate contractanta"),
        "autoritate_cui": _c("cui autoritate contractanta"),
        "nr_anunt_atribuire": _c("numar anunt atribuire"),
        "data_publicare": _c("data publicare"),
        "nr_contract": _c("numar contract"),
        "data_contract": _c("data contract"),
        "descriere_modificari": _c("descrierea modificarilor"),
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
# against the source before being listed.
KNOWN_COLUMN_GAPS: dict[tuple[str, str], dict[str, Any]] = {
    ("contracte", "numar_oferte"): {
        "years": range(2019, 2027),
        "reason": (
            "The publisher stopped including NumarOfertePrimite after 2018. This is a "
            "real loss of transparency, not a mapping failure: single-bidder rate is "
            "the strongest indicator in the literature and cannot be computed for these "
            "years from the bulk exports."
        ),
    },
    ("contracte", "subcontractat"): {
        "years": (2016, 2017, 2018),
        "reason": (
            "Reporting convention change, not absence. 2016-2017 record only 'DA' and "
            "leave the rest blank; 2019 onward record 'DA' and 'NU' explicitly. A blank "
            "in the early years therefore means 'not subcontracted' — reading it as "
            "missing would understate subcontracting."
        ),
    },
    ("contracte", "valoare_estimata_ron"): {
        "years": range(2022, 2027),
        "reason": (
            "The estimate stopped being carried on the contract row. For later years it "
            "must come from the initiation notice, which reintroduces the framework "
            "double-counting problem."
        ),
    },
    ("contracte", "licitatie_electronica"): {
        "years": range(2019, 2027),
        "reason": "CuLicitatieElectronica appears only in the 2016-2018 exports.",
    },
    ("initiere", "tip_anunt"): {
        "years": (2016, 2017, 2018),
        "reason": (
            "The 2016-2018 initiation exports carry no notice-type column at all; "
            "TIP_ANUNT first appears in the 2019 snake-case format. Every row in those "
            "files is a participation notice, so the column carried no information."
        ),
    },
    ("initiere", "stare_procedura"): {
        "years": range(2016, 2027),
        "reason": (
            "Procedure status is not published in any bulk initiation export examined "
            "(2016-2026); it exists only in the SEAP API. Kept in the schema because "
            "the API path populates it."
        ),
    },
    ("initiere", "loturi"): {
        "years": range(2016, 2027),
        "reason": (
            "The lot-split flag is not published in the bulk initiation exports; only "
            "the contracts table carries lot numbers."
        ),
    },
}


def is_known_gap(table: str, column: str, year: int) -> dict[str, Any] | None:
    """Return the documented reason a column is empty that year, or None."""
    entry = KNOWN_COLUMN_GAPS.get((table, column))
    if entry and year in entry["years"]:
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
    """The 2017 exports are caret-delimited; later CSVs use comma or semicolon."""
    head = text.split("\n", 1)[0]
    return max("^;,\t", key=head.count)


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
    reader = csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _rows_xlsx(blob: bytes) -> tuple[list[str], list[list[str]]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    ws = wb.active
    # Read-only mode trusts the sheet's declared dimension, and several exports declare
    # a false one: the 2019-2020 direct-acquisition files claim max_row=1, max_col=1
    # despite holding 500,000+ rows across 21 columns. Trusting that silently yields an
    # empty table from a 134 MB file. reset_dimensions() makes openpyxl derive the real
    # extent from the data while still streaming.
    ws.reset_dimensions()
    it = ws.iter_rows(values_only=True)
    try:
        header = ["" if h is None else str(h) for h in next(it)]
    except StopIteration:
        wb.close()
        return [], []
    rows = [["" if v is None else str(v) for v in row] for row in it]
    wb.close()
    return header, rows


def _rows_xls(blob: bytes) -> tuple[list[str], list[list[str]]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=blob)
    sheet = book.sheet_by_index(0)
    if sheet.nrows == 0:
        return [], []
    header = [str(c) for c in sheet.row_values(0)]
    rows = [[str(c) for c in sheet.row_values(i)] for i in range(1, sheet.nrows)]
    return header, rows


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


def map_columns(header: list[str], spec: TableSpec) -> dict[str, int]:
    """canonical name -> column index, for the columns this file actually has."""
    lookup = {_header_key(h): i for i, h in enumerate(header) if h}
    mapping: dict[str, int] = {}
    for canonical, aliases in spec.columns.items():
        for alias in aliases:
            idx = lookup.get(_header_key(alias))
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
        rec["sursa"] = source
        records.append(rec)
    return records, unmapped, malformed


# ----------------------------------------------------------------------- discovery

@dataclass(frozen=True)
class Resource:
    year: int
    table: TableSpec
    name: str
    url: str

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
    return found


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
