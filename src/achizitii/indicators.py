"""Risk indicators over the bulk exports.

Design follows the discipline visible in ProZorro's risk engine (see docs/prior-art.md):
every indicator declares its legal basis, the scope it applies to, and the dates between
which it is valid. Nothing is copied from that project — it carries no licence.

Three rules govern everything here:

1. **An indicator reports a pattern, never a conclusion.** Each finding is
   "requires review", with the source records attached. Wording that asserts
   wrongdoing does not belong in this file or its output.
2. **Deterministic only.** Every indicator below is arithmetic over public fields.
   No model is involved, and none is needed. That is what makes a finding checkable
   by the authority it names.
3. **Indicators are dated.** Procurement thresholds and rules change. Applying today's
   threshold to 2018 data manufactures findings, so each rule carries validity dates
   and is skipped outside them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# ---------------------------------------------------------------- legal thresholds
#
# Ceiling below which a contracting authority may buy directly, without a competitive
# procedure (Legea 98/2016, art. 7 alin. 5). The ceiling has been raised repeatedly, so
# it is a dated schedule rather than a constant.
#
# Getting this from the legal text alone turned out to be unreliable. The freely
# available consolidations disagree: the originally published form gives 132,519 /
# 441,730 lei, secondary sources quote 270,120 for goods/services and variously 900,000
# or 900,400 for works, and dated consolidations sit behind paywalls. A wrong ceiling
# does not merely weaken the indicator — it invents findings.
#
# So the operative ceiling is instead DETECTED FROM THE DATA (`detect_ceiling`), and the
# legal values below serve only as corroboration. The distribution of direct-acquisition
# values collapses at the ceiling, because exceeding it is unlawful; that cliff is the
# ceiling actually in force, whichever amending act set it.
#
# `source` records where a value came from. `verified` means it has been confirmed
# against observed data, not merely quoted somewhere.
THRESHOLDS: list[dict[str, Any]] = [
    {
        "from": date(2023, 1, 1),
        "to": None,
        "goods_services": 270_120.0,
        "works": 900_400.0,
        "verified": True,
        "source": (
            "Both confirmed against H1 2026 data (1,053,138 goods/services records). "
            "Goods/services: 1,445 in (260,000, 270,120] against 21 above it; only "
            "0.035% of all records exceed the ceiling, and sampled exceedances are "
            "misclassifications (e.g. road maintenance works booked as 'Servicii', "
            "which carries the higher works ceiling). "
            "Works: 197 in (890,000, 900,000], 20 in (900,000, 900,400], and ZERO in "
            "(900,400, 910,000] — which excludes 900,000 and pins 900,400."
        ),
    },
]


def threshold_for(day: date, category: str) -> float | None:
    """Declared ceiling for a date, or None when it is not established.

    None is a refusal, not a permission: callers must skip rather than substitute a
    default.
    """
    key = "works" if category == "works" else "goods_services"
    for row in THRESHOLDS:
        if not row["verified"]:
            continue
        if day < row["from"]:
            continue
        if row["to"] and day > row["to"]:
            continue
        return row.get(key)
    return None


# Detection parameters. A cliff only counts when there is enough mass below it to be
# meaningful and the drop is unambiguous.
CEILING_MIN_BELOW = 100
"""Minimum acquisitions in the window below a candidate ceiling."""

CEILING_MIN_RATIO = 8.0
"""Below:above density ratio required to call a cliff a legal ceiling."""

CEILING_WINDOW = 20_000.0
"""Lei either side of a candidate used to measure the drop."""


CORROBORATION_MAX_EXCEEDANCE_PCT = 0.5
"""A declared ceiling is corroborated when under this share of records exceed it."""


def corroborate_ceiling_sql(category: str = "goods_services") -> str:
    """Test a declared ceiling against the data, rather than assuming the cliff scan found it.

    `detect_ceiling_sql` locates the cliff but not its exact value: every candidate above
    the true ceiling scores alike, and the +/-20,000 lei window smears the estimate by a
    few thousand lei. This instead asks a sharper question — what share of records exceed
    a *specific* declared figure — which is what actually validates a legal value.

    A ceiling is corroborated when exceedances are a rounding error. Some are expected:
    misclassified works carry a higher ceiling and legitimately appear above the
    goods/services figure.
    """
    types = (
        "('lucrari')" if category == "works" else "('furnizare','servicii')"
    )
    return f"""
    WITH d AS (
      SELECT an, TRY_CAST(valoare_ron AS DOUBLE) v FROM achizitii_directe
      WHERE categorie IN {types}
        AND TRY_CAST(valoare_ron AS DOUBLE) > 0
    )
    SELECT an,
           count(*) AS n_total,
           count(*) FILTER (WHERE v > $prag * 0.96 AND v <= $prag) AS n_just_below,
           count(*) FILTER (WHERE v > $prag) AS n_peste,
           round(100.0 * count(*) FILTER (WHERE v > $prag) / count(*), 4) AS pct_peste,
           max(v) FILTER (WHERE v <= $prag) AS max_sub_plafon
    FROM d GROUP BY an ORDER BY an
    """


def detect_ceiling_sql(window: float = CEILING_WINDOW) -> str:
    """SQL that finds the sharpest density cliff per year.

    Direct acquisitions cannot lawfully exceed the ceiling, so their value distribution
    ends abruptly at it. Scanning candidate cutoffs and scoring each by the ratio of
    mass just below to mass just above recovers the ceiling in force that year without
    relying on a legal consolidation.
    """
    return f"""
    WITH d AS (
      SELECT an, TRY_CAST(valoare_ron AS DOUBLE) v
      FROM achizitii_directe
      WHERE categorie IN ('furnizare','servicii')
        AND TRY_CAST(valoare_ron AS DOUBLE) BETWEEN 20000 AND 2000000
    ),
    candidates AS (
      SELECT DISTINCT an, (floor(v / 1000) * 1000) AS c FROM d
    ),
    scored AS (
      SELECT c.an, c.c AS cutoff,
             count(*) FILTER (WHERE d.v > c.c - {window} AND d.v <= c.c) AS n_below,
             count(*) FILTER (WHERE d.v > c.c AND d.v <= c.c + {window}) AS n_above,
             max(d.v) FILTER (WHERE d.v <= c.c) AS max_below
      FROM candidates c JOIN d ON d.an = c.an
      GROUP BY c.an, c.c
    ),
    ranked AS (
      SELECT an, cutoff, n_below, n_above, max_below,
             n_below::DOUBLE / greatest(n_above, 1) AS ratio,
             row_number() OVER (
               PARTITION BY an
               -- Smallest cutoff among equally sharp cliffs: every candidate above the
               -- true ceiling scores the same, so the tightest bound is the honest one.
               ORDER BY n_below::DOUBLE / greatest(n_above, 1) DESC, cutoff ASC
             ) AS rn
      FROM scored WHERE n_below >= {CEILING_MIN_BELOW}
    )
    SELECT an,
           cutoff AS cliff_at,
           max_below AS max_valoare_observata,
           n_below, n_above, round(ratio, 1) AS ratio
    FROM ranked WHERE rn = 1 AND ratio >= {CEILING_MIN_RATIO}
    ORDER BY an
    """


# ------------------------------------------------------------------------ rule type

@dataclass(frozen=True)
class Indicator:
    identifier: str
    name_ro: str
    description_ro: str
    legal_basis: str
    """The provision the pattern relates to. A finding without one is not defensible."""
    rationale: str
    """Why this rule exists — what behaviour it is designed to surface."""
    applies_to: tuple[str, ...]
    """Canonical table keys this rule reads."""
    valid_from: date = date(2023, 1, 1)
    valid_to: date | None = None
    sql: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def active_on(self, day: date) -> bool:
        return day >= self.valid_from and (self.valid_to is None or day <= self.valid_to)


# --------------------------------------------------------------------------- rules

PRAG_01 = Indicator(
    identifier="prag-01",
    name_ro="Concentrare a valorilor imediat sub plafonul achiziției directe",
    description_ro=(
        "Numără achizițiile directe de produse/servicii ale căror valori se grupează "
        "imediat sub plafonul legal, comparativ cu benzile de valoare inferioare. O "
        "concentrare puternică indică valori stabilite pentru a rămâne sub plafon, nu "
        "rezultate dintr-o estimare a necesarului."
    ),
    legal_basis="Legea 98/2016, art. 7 — plafoane pentru achiziția directă",
    rationale=(
        "Împărțirea sau dimensionarea unei achiziții pentru a evita procedura "
        "competitivă este cea mai directă formă de eludare a legii și este vizibilă "
        "statistic fără nicio inferență."
    ),
    applies_to=("achizitii_directe",),
    sql="""
    WITH d AS (
      SELECT TRY_CAST(valoare_ron AS DOUBLE) v, tip_contract, autoritate, autoritate_cui,
             furnizor, nr_achizitie, data_publicare
      FROM achizitii_directe
      WHERE categorie IN ('furnizare','servicii')
    ),
    banda AS (
      SELECT *, floor(v / 10000) * 10000 AS banda FROM d
      WHERE v BETWEEN $prag * 0.6 AND $prag
    ),
    densitate AS (SELECT banda, count(*) n FROM banda GROUP BY 1),
    baza AS (SELECT median(n) mediana FROM densitate WHERE banda < $prag - 20000)
    SELECT 'prag-01' AS indicator,
           b.autoritate, b.autoritate_cui, b.furnizor, b.nr_achizitie, b.data_publicare,
           b.v AS valoare_ron,
           round(100.0 * b.v / $prag, 2) AS pct_din_plafon,
           (SELECT mediana FROM baza) AS densitate_de_referinta
    FROM banda b
    WHERE b.v >= $prag * 0.98
    ORDER BY b.v DESC
    """,
)

FARA_COMPETITIE_01 = Indicator(
    identifier="fara-competitie-01",
    name_ro="Atribuire fără publicarea prealabilă a unui anunț",
    description_ro=(
        "Listează contractele atribuite prin negociere fără publicare prealabilă sau "
        "prin norme proprii, ordonate după valoare. Procedura este legală în situații "
        "expres prevăzute, dar elimină complet competiția."
    ),
    legal_basis="Legea 98/2016, art. 104 — negocierea fără publicare prealabilă",
    rationale=(
        "Este categoria în care lipsa competiției este totală, deci și cea în care "
        "abuzul are cel mai mare impact per contract."
    ),
    applies_to=("fara_anunt",),
    sql="""
    SELECT 'fara-competitie-01' AS indicator,
           autoritate, autoritate_cui, furnizor, furnizor_cui,
           nr_contract, data_contract, tip_procedura, cpv,
           TRY_CAST(valoare_ron AS DOUBLE) AS valoare_ron
    FROM fara_anunt
    WHERE TRY_CAST(valoare_ron AS DOUBLE) >= $valoare_minima
    ORDER BY TRY_CAST(valoare_ron AS DOUBLE) DESC
    """,
    params={"valoare_minima": 100_000.0},
)

DIVIZARE_01 = Indicator(
    identifier="divizare-01",
    name_ro="Achiziții repetate, același obiect și același furnizor, într-un interval scurt",
    description_ro=(
        "Grupează achizițiile directe după autoritate, cod CPV și furnizor și "
        "semnalează grupurile numeroase apărute într-un interval scurt, a căror valoare "
        "cumulată ar fi depășit plafonul achiziției directe."
    ),
    legal_basis=(
        "Legea 98/2016, art. 11 — interzicerea divizării contractului în scopul "
        "evitării procedurii de atribuire"
    ),
    rationale=(
        "Nouă contracte încheiate în aceeași zi cu același furnizor, pentru același cod "
        "CPV, produc împreună o valoare care ar fi impus licitație deschisă."
    ),
    applies_to=("achizitii_directe",),
    sql="""
    WITH d AS (
      SELECT autoritate, autoritate_cui, cpv, furnizor, furnizor_cui, nr_achizitie,
             TRY_CAST(valoare_ron AS DOUBLE) v,
             TRY_CAST(data_publicare AS TIMESTAMP) t
      FROM achizitii_directe
      WHERE valoare_ron IS NOT NULL AND cpv IS NOT NULL AND furnizor IS NOT NULL
    ),
    g AS (
      SELECT autoritate_cui, any_value(autoritate) autoritate, cpv, furnizor,
             any_value(furnizor_cui) furnizor_cui,
             count(*) n, sum(v) total_ron, min(t) prima, max(t) ultima,
             date_diff('day', min(t), max(t)) zile
      FROM d GROUP BY autoritate_cui, cpv, furnizor
      HAVING count(*) >= $min_contracte
         AND date_diff('day', min(t), max(t)) <= $max_zile
         AND sum(v) > $prag
    )
    SELECT 'divizare-01' AS indicator, autoritate, autoritate_cui, cpv, furnizor,
           furnizor_cui, n AS numar_achizitii, round(total_ron, 2) AS total_ron,
           zile, prima, ultima,
           round(total_ron / $prag, 2) AS ori_peste_plafon
    FROM g ORDER BY total_ron DESC
    """,
    params={"min_contracte": 5, "max_zile": 30},
)

DEPENDENTA_01 = Indicator(
    identifier="dependenta-01",
    name_ro="Concentrare a bugetului de achiziții directe către un singur furnizor",
    description_ro=(
        "Calculează, pentru fiecare autoritate, ponderea bugetului de achiziții directe "
        "care revine unui singur furnizor. Se raportează doar relațiile cu un număr "
        "semnificativ de achiziții, pentru a exclude cazurile în care o singură "
        "cumpărare mare domină un buget mic."
    ),
    legal_basis=(
        "Legea 98/2016, art. 2 — principiile nediscriminării, tratamentului egal și "
        "promovării concurenței"
    ),
    rationale=(
        "O pondere foarte mare, susținută pe multe achiziții, poate reflecta o piață "
        "locală restrânsă sau o relație preferențială. Indicatorul nu distinge între "
        "cele două; el arată unde merită verificat."
    ),
    applies_to=("achizitii_directe",),
    sql="""
    WITH d AS (
      SELECT autoritate_cui, any_value(autoritate) OVER (PARTITION BY autoritate_cui) aut,
             furnizor, furnizor_cui, TRY_CAST(valoare_ron AS DOUBLE) v
      FROM achizitii_directe WHERE valoare_ron IS NOT NULL
    ),
    total AS (
      SELECT autoritate_cui, any_value(aut) autoritate, sum(v) buget, count(*) n_total
      FROM d GROUP BY autoritate_cui HAVING sum(v) >= $buget_minim
    ),
    per_furnizor AS (
      SELECT autoritate_cui, furnizor, any_value(furnizor_cui) furnizor_cui,
             sum(v) valoare, count(*) n
      FROM d GROUP BY autoritate_cui, furnizor
      HAVING count(*) >= $min_achizitii
    )
    SELECT 'dependenta-01' AS indicator, t.autoritate, t.autoritate_cui,
           f.furnizor, f.furnizor_cui, f.n AS numar_achizitii,
           round(f.valoare, 2) AS valoare_furnizor_ron,
           round(t.buget, 2) AS buget_total_ron,
           round(100.0 * f.valoare / t.buget, 1) AS pondere_pct
    FROM total t JOIN per_furnizor f USING (autoritate_cui)
    WHERE f.valoare / t.buget >= $pondere_minima
    ORDER BY t.buget DESC
    """,
    params={"buget_minim": 200_000.0, "min_achizitii": 10, "pondere_minima": 0.75},
)

MODIFICARE_01 = Indicator(
    identifier="modificare-01",
    name_ro="Creștere a valorii contractului după semnare",
    description_ro=(
        "Compară valoarea contractului înainte și după actele adiționale publicate și "
        "semnalează creșterile importante. Include justificarea publicată de autoritate."
    ),
    legal_basis="Legea 98/2016, art. 221 — modificarea contractului de achiziție publică",
    rationale=(
        "O ofertă câștigată la un preț mic și majorată substanțial ulterior are același "
        "efect economic ca o ofertă mai mare respinsă la atribuire, dar fără competiție."
    ),
    applies_to=("modificari",),
    sql="""
    WITH m AS (
      SELECT autoritate, autoritate_cui, nr_contract, data_contract, descriere_modificari,
             TRY_CAST(valoare_inainte_ron AS DOUBLE) inainte,
             TRY_CAST(valoare_dupa_ron AS DOUBLE) dupa
      FROM modificari
    )
    SELECT 'modificare-01' AS indicator, autoritate, autoritate_cui, nr_contract,
           data_contract, round(inainte, 2) AS valoare_inainte_ron,
           round(dupa, 2) AS valoare_dupa_ron,
           round(dupa - inainte, 2) AS crestere_ron,
           round(100.0 * (dupa - inainte) / nullif(inainte, 0), 1) AS crestere_pct,
           descriere_modificari AS justificare_publicata
    FROM m
    WHERE inainte > 0 AND dupa > inainte
      AND (dupa - inainte) / inainte >= $crestere_minima
      AND (dupa - inainte) >= $crestere_minima_ron
    ORDER BY (dupa - inainte) DESC
    """,
    params={"crestere_minima": 0.15, "crestere_minima_ron": 50_000.0},
)

INDICATORS: tuple[Indicator, ...] = (
    PRAG_01,
    FARA_COMPETITIE_01,
    DIVIZARE_01,
    DEPENDENTA_01,
    MODIFICARE_01,
)
INDICATORS_BY_ID = {i.identifier: i for i in INDICATORS}
