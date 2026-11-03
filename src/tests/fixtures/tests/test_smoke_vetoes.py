"""Veto fixtures (§7): tier "smoke" vetoes a gated removal."""

import pytest

from prod import calc


@pytest.mark.smoke
def test_smoke_lowvalue_dup():
    result = calc.add(2, 2)
    assert result == 4


def test_smoke_pair_full():
    result = calc.add(2, 2)
    plus = calc.subtract(4, 2)
    assert result == 4
    assert plus == 2
