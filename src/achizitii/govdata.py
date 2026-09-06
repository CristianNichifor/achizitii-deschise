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
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import CONTACT
from .normalize import fold

log = logging.getLogger(__name__)

CKAN = "https://data.gov.ro/api/3/action/package_show"
DATASET = "achizitii-publice-{year}"


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
        "cpv_denumire": _c("denumire cpv", "cpvcodeid"),
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
        "nr_anunt_atribuire": _c("numar anunt atribuire", "numaranunt"),
        "data_publicare": _c("data publicare", "dataanunt"),
        "tip_contract": _c("tip contract", "tipcontract"),
        "criteriu_atribuire": _c("tip criterii de atribuire", "criteriu de atribuire"),
        "cpv": _c("cod cpv", "cpvcode"),
        "cpv_denumire": _c("denumire cpv"),
        "nr_lot": _c("numar lot"),
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
    match=re.compile(r"(?i)anun[tț]uri\s+de\s+ini[tț]iere"),
    columns={
        "autoritate": _c("autoritate contractanta"),
        "autoritate_cui": _c("cui autoritate contractanta"),
        "tip_anunt": _c("tip anunt"),
        "tip_procedura": _c("tip procedura"),
        "stare_procedura": _c("stare procedura"),
        "nr_anunt_initiere": _c("numar anunt initiere"),
        "data_publicare": _c("data publicare"),
        "tip_contract": _c("tip contract"),
        "modalitate_atribuire": _c("modalitate de atribuire"),
        "loturi": _c("contractul este impartit in loturi"),
        "denumire": _c("denumire procedura"),
        "cpv": _c("cod cpv"),
        "cpv_denumire": _c("denumire cpv"),
        "valoare_estimata_ron": _c("valoare estimata procedura ron"),
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
    """Normalise a source header for alias matching: fold + drop punctuation."""
    return re.sub(r"\s+", " ", _PUNCT.sub(" ", fold(name))).strip()


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


def to_records(
    header: list[str], rows: list[list[str]], spec: TableSpec, source: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map rows onto the canonical schema. Returns (records, unmapped_headers)."""
    mapping = map_columns(header, spec)
    unmapped = [
        h for i, h in enumerate(header) if h and i not in set(mapping.values())
    ]
    records: list[dict[str, Any]] = []
    for row in rows:
        rec: dict[str, Any] = {c: None for c in spec.columns}
        for canonical, idx in mapping.items():
            if idx < len(row):
                value = (row[idx] or "").strip()
                rec[canonical] = value or None
        if not any(rec.values()):
            continue
        rec["sursa"] = source
        records.append(rec)
    return records, unmapped


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


def discover(year: int, client: httpx.Client) -> list[Resource]:
    """Resources for one year, classified by logical table.

    Resources whose name matches no known table (SAD invitations, award notifications)
    are skipped and logged rather than silently dropped.
    """
    r = client.get(CKAN, params={"id": DATASET.format(year=year)})
    if r.status_code == 404:
        log.warning("no dataset for %d", year)
        return []
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
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
    r = client.get(resource.url, follow_redirects=True, timeout=300.0)
    r.raise_for_status()
    path.write_bytes(r.content)
    return r.content


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": f"achizitii-deschise/0.1 (+{CONTACT})"},
        timeout=120.0,
        follow_redirects=True,
    )
