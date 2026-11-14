"""Invariants of the seed data that other code depends on (no database needed)."""

from db.seed import IDENTITIES, LOCATIONS

LOCATION_IDS = {loc["_id"] for loc in LOCATIONS}
READERS = {i["_id"]: i["location_id"] for i in IDENTITIES if i["kind"] == "scanner"}


def test_every_scanner_reader_sits_at_a_location_that_exists():
    nowhere = {r: loc for r, loc in READERS.items() if loc not in LOCATION_IDS}
    assert nowhere == {}  # e.g. a reader at "FZA" would make the scanner reject every scan it signs


def test_each_place_a_sample_can_be_scanned_has_its_own_reader_with_the_same_id_as_before():
    assert READERS["scanner:reader-fza"] == "FZ-A"
    assert READERS["scanner:reader-fzb"] == "FZ-B"
    assert READERS["scanner:reader-seq3"] == "SEQ-3"
    assert READERS["scanner:reader-forlab"] == "FOR-LAB"
    assert READERS["scanner:reader-corr1"] == "CORR-1"
