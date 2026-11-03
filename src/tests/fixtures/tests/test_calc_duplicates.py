"""Redundant/duplicate pair 1 (§5.3): alpha and beta are the same test modulo
renames. Both assert `calc.add(1, 2) == 3`; coverage fingerprints are
identical and the assertion skeletons match modulo rename."""

from prod import calc


def test_add_pair_alpha():
    result = calc.add(1, 2)
    assert result == 3


def test_add_pair_beta():
    value = calc.add(1, 2)
    assert value == 3
