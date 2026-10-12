"""Tests für die Registry-Auflösung."""

import pytest

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.registry import resolve


def test_resolve_returns_entry() -> None:
    assert resolve({"a": 1}, "a", "Verfahren") == 1


def test_unknown_name_lists_all_valid_names() -> None:
    with pytest.raises(ConfigError, match=r"„xyz“\. Gültig sind: alpha, beta\."):
        resolve({"beta": 2, "alpha": 1}, "xyz", "Verfahren")
