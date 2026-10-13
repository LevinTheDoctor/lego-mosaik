"""Tests für Katalog-Import und PostgreSQL-Katalogquelle."""

import shutil
from pathlib import Path

import pytest
from conftest import kern
from lego_mosaik.config import SourceConfig
from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.sources.protocol import FileInfo, FileStatus, FileUpdate, UpdateReport

from lego_mosaik_cloud.app import CloudServices
from lego_mosaik_cloud.catalog.importer import PostgresCatalogImporter
from lego_mosaik_cloud.catalog.source import PostgresCatalogSource


def test_written_catalog_loads_unchanged(imported: CloudServices) -> None:
    expected = kern.make_catalog()
    loaded = imported.catalog.load_catalog()
    assert loaded.colors == expected.colors
    assert loaded.part_types == expected.part_types
    assert loaded.available == expected.available
    assert loaded.element_ids == expected.element_ids


def test_missing_catalog_is_a_clear_error(services: CloudServices) -> None:
    with pytest.raises(CatalogError, match="noch kein Teilekatalog"):
        services.catalog.load_catalog()
    assert all(info.outdated and info.modified is None for info in services.importer.status())


def test_other_pods_see_a_new_import(imported: CloudServices) -> None:
    other_pod = PostgresCatalogSource(imported.engine, refresh_seconds=0)
    first = other_pod.load_catalog()
    imported.importer.write(kern.make_catalog(), ["colors.csv.gz"])
    assert other_pod.load_catalog() is not first  # neue Version → neu geladen


class _CopyUpdater:
    """Statt Download: kopiert die Mini-Dumps der Kern-Fixtures in den Zielordner."""

    def __init__(self, dumps: Path, target: Path) -> None:
        self._dumps, self._target = dumps, target

    def update(self, *, force: bool = False) -> UpdateReport:
        shutil.copytree(self._dumps, self._target, dirs_exist_ok=True)
        names = sorted(path.name for path in self._dumps.iterdir())
        return UpdateReport(
            tuple(FileUpdate(name, FileStatus.DOWNLOADED, "kopiert", True) for name in names)
        )

    def status(self) -> tuple[FileInfo, ...]:
        return ()


def test_update_downloads_parses_and_writes(
    services: CloudServices, source_config: SourceConfig
) -> None:
    source = services.config.source
    importer = PostgresCatalogImporter(
        services.engine,
        source,
        updater_factory=lambda s: _CopyUpdater(source_config.data_dir, s.data_dir),
    )
    report = importer.update()
    assert report.downloaded_any
    catalog = services.catalog.load_catalog()
    assert set(catalog.colors) == {cid for cid, *_ in kern.COLOR_ROWS}  # ohne Sonderfarben
    assert "9999x" not in catalog.part_types  # nur Teile aus teil_geometrie.csv
    # Gleich danach ist der Katalog aktuell: kein zweiter Download
    again = importer.update()
    assert {u.status for u in again.files} == {FileStatus.UP_TO_DATE}
