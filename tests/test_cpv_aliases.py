"""Curated equivalences, and the three patterns that must never become one.

Measuring the archive before writing `data/cpv_aliases.yml` changed what belongs in it.
31.7% of specific-product rows already appear under more than one CPV code, and that
needs no curation — `preturi_produs` groups on the description and lets the code vary.
Curation is only needed for the opposite case: the same object under different words,
where nothing automatic can help because the descriptions share no tokens.

Three patterns look like synonymy in the data and would corrupt a benchmark:

    hierarchy    03221400 "Varza" / 03221410 "Varza alba" — a kind of, not a name for
    catch-alls   33690000 "Diverse medicamente" absorbing specific drug classes
    mis-filing   30125100 toner cartridges / 35331500 AMMUNITION cartridges

Each is asserted absent below.
"""

from __future__ import annotations

import yaml

from achizitii.produs import _aliases, canonical_cpv, product_key


def _spec() -> dict:
    from pathlib import Path

    from achizitii.config import ROOT

    return yaml.safe_load((Path(ROOT) / "data" / "cpv_aliases.yml").read_text(encoding="utf-8"))


def test_the_file_loads_and_is_wired_in() -> None:
    """It was referenced by README, CONTRIBUTING and cluster.py before it existed."""
    codes, heads = _aliases()
    assert codes, "no CPV equivalences loaded"
    assert heads, "no description synonyms loaded"


def test_copier_paper_codes_collapse() -> None:
    """Three official labels for one product; "hartie copiator a4" uses all three."""
    assert canonical_cpv("30197643-1") == "30197642"
    assert canonical_cpv("30197644") == "30197642"
    assert product_key("Hartie copiator A4", "30197642").key == (
        product_key("Hartie xerox A4", "30197644").key
    )


def test_different_words_same_object_merge() -> None:
    """The motivating example from CONTRIBUTING.md, and the reason the file exists."""
    bench = product_key("Mobilier odihna exterior", "39113600")
    assert bench.key == product_key("Mobilier urban odihna", "39113600").key
    assert "banci parc" in bench.key


def test_brands_are_not_broken_by_synonyms() -> None:
    """Rewriting happens before brand detection; it must not disturb ordinary rows."""
    assert product_key("Toner HP CF380X", "30125100").brand == "hp"


def test_no_hierarchical_pairs_are_declared_equivalent() -> None:
    """A child code is a KIND of the parent, not another name for it.

    These share a code prefix, which is exactly how CPV expresses hierarchy — so a
    declared equivalence between two codes sharing six digits is almost certainly this
    mistake rather than a real synonym.
    """
    def significant(code: str) -> str:
        """CPV pads with trailing zeros, so stripping them leaves the real depth."""
        return code.rstrip("0")

    for group in _spec()["coduri_echivalente"]:
        canonical = significant(str(group["canonic"]))
        for raw in group["aliase"]:
            alias = significant(str(raw))
            # Siblings share a prefix as well (30197642 / 30197643), so a shared prefix
            # is not the signal. Parent/child is when one code IS a prefix of the other
            # once padding is removed: 7152 contains 715210, 032214 contains 0322141.
            assert not (
                alias.startswith(canonical) or canonical.startswith(alias)
            ), (
                f"{raw} and {group['canonic']} are a parent/child pair — one code is a "
                "prefix of the other. CPV already encodes that; a child is a kind of "
                "the parent, not another name for it."
            )


def test_known_traps_are_absent() -> None:
    """The specific pairs the data suggests and that would be wrong to encode."""
    codes, _ = _aliases()
    # Ammunition must never be merged with toner, however similar the wording.
    assert codes.get("35331500") != "30125100"
    assert codes.get("30125100") != "35331500"
    # "Diverse medicamente" is a catch-all buyers reach for, not a synonym.
    for specific in ("33661600", "33632200", "33661200", "33622200"):
        assert codes.get(specific) != "33690000", (
            f"{specific} mapped into the 33690000 catch-all; co-occurrence is not "
            "equivalence"
        )
    # Cabbage / white cabbage is hierarchy.
    assert codes.get("03221410") != "03221400"


def test_every_group_states_how_it_was_checked() -> None:
    """A merge with no stated reason cannot be reviewed by anyone."""
    spec = _spec()
    for section in ("coduri_echivalente", "sinonime"):
        for group in spec[section]:
            assert len(str(group.get("motiv", ""))) > 60, (
                f"{group.get('canonic')} in {section} gives no checkable reason"
            )
            assert group.get("aliase"), f"{group.get('canonic')} lists no aliases"


def test_canonical_codes_are_not_themselves_aliases() -> None:
    """A -> B -> C would resolve differently depending on lookup order."""
    codes, heads = _aliases()
    for alias, canonical in codes.items():
        assert canonical not in codes, f"{alias} -> {canonical}, which is itself an alias"
    for alias, canonical in heads.items():
        assert canonical not in heads, f"{alias} -> {canonical}, which is itself an alias"
