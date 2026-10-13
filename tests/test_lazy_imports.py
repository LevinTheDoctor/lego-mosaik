"""Lazy Imports (PEP 810): Der Start darf keine schweren Bibliotheken laden."""

import json
import subprocess
import sys

HEAVY_MODULES = ("skimage", "fastapi", "uvicorn", "numpy", "PIL", "lego_mosaik.pricing")


def _loaded_after(code: str) -> dict[str, bool]:
    """Führt ``code`` in einem frischen Prozess aus und meldet, welche Module geladen sind."""
    check = (
        f"{code}; import json, sys; "
        f"print(json.dumps([m in sys.modules for m in {list(HEAVY_MODULES)!r}]))"
    )
    output = (
        subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, check=True)
        .stdout.strip()
        .splitlines()[-1]
    )
    return dict(zip(HEAVY_MODULES, json.loads(output), strict=True))


def test_importing_main_loads_no_heavy_modules() -> None:
    assert not any(_loaded_after("import lego_mosaik.main").values())


def test_help_loads_no_heavy_modules() -> None:
    code = (
        "import lego_mosaik.main as m, typer.testing; "
        "cli = m.create_cli(m.CONFIG, m.build_pipeline, None, m.build_price_service); "
        "typer.testing.CliRunner().invoke(cli, ['--hilfe'])"
    )
    loaded = _loaded_after(code)
    assert not loaded["skimage"] and not loaded["fastapi"]
    assert not loaded["lego_mosaik.pricing"]
