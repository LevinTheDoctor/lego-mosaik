"""Golden-File-Tests: Farbraster und LDraw-Datei müssen exakt gleich bleiben.

Golden Files sind gespeicherte, einmal geprüfte Ergebnisse. Jede ungewollte
Verhaltensänderung (auch bei einer späteren Portierung nach Java oder Rust)
fällt dadurch sofort auf. Bewusste Änderungen übernimmt man mit::

    uv run pytest --golden-aktualisieren
"""

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from conftest import color_blocks, gray_gradient, make_pipeline
from PIL import Image

from lego_mosaik.config import AppConfig
from lego_mosaik.domain.models import IndexGrid, LegoColor

GOLDEN_DIR = Path(__file__).parent / "golden"

# Name → (Testbild als RGB-Array, Dithering-Verfahren)
CASES = {
    "bloecke_floyd": (color_blocks(32), "floyd_steinberg"),
    "verlauf_floyd": (gray_gradient(32), "floyd_steinberg"),
    "verlauf_ordered": (gray_gradient(32), "ordered"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_files(
    case: str, pipeline_config: AppConfig, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    pixels, method = CASES[case]
    image_path = tmp_path / f"{case}.png"
    Image.fromarray(pixels).save(image_path)
    config = dataclasses.replace(
        pipeline_config,
        dithering=dataclasses.replace(pipeline_config.dithering, method=method),
        outputs=("ldraw",),
    )
    result = make_pipeline(config).run(image_path, title=case)
    actual = {
        f"{case}_raster.json": _grid_json(result.mosaic.color_grid, result.mosaic.palette),
        f"{case}.ldr": (result.target_dir / "mosaik.ldr").read_text(encoding="utf-8"),
    }
    for name, content in actual.items():
        _compare_or_update(GOLDEN_DIR / name, content, request)


def _grid_json(grid: IndexGrid, palette: Sequence[LegoColor]) -> str:
    """Raster als JSON mit Rebrickable-IDs statt Palettenindizes (lesbarer, stabiler)."""
    ids = [[palette[index].id for index in row] for row in grid.tolist()]
    return json.dumps({"raster": ids}, indent=1) + "\n"


def _compare_or_update(path: Path, content: str, request: pytest.FixtureRequest) -> None:
    if request.config.getoption("--golden-aktualisieren"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return
    assert path.is_file(), f"{path.name} fehlt – mit --golden-aktualisieren erzeugen."
    assert path.read_text(encoding="utf-8") == content, f"{path.name} weicht ab."
