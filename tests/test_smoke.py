"""Rauchtest: Das Paket lässt sich importieren."""

import lego_mosaik


def test_package_is_importable() -> None:
    assert lego_mosaik.__doc__ is not None
