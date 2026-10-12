"""Bildaufbereitung mit Pillow: laden, Kontrast/Sättigung, einpassen."""

from pathlib import Path

lazy import numpy as np

from lego_mosaik.domain.models import FitMode, Resampling, RgbTuple
from lego_mosaik.imaging.fitting import enhance, fit_image
from lego_mosaik.imaging.loader import load_image
from lego_mosaik.imaging.protocol import RgbImage


class PillowImagePreparer:
    """Erfüllt das Protocol ``ImagePreparer`` (Facade über loader und fitting).

    Facade Pattern: Eine einfache Methode ``prepare`` verbirgt die einzelnen
    Schritte Laden, Verbessern und Einpassen vor der Pipeline.

    Args:
        fit_mode: Zuschneiden oder Einpassen.
        resampling: Skalierverfahren.
        contrast: Kontrastfaktor (1.0 = unverändert).
        saturation: Sättigungsfaktor (1.0 = unverändert).
    """

    def __init__(
        self, *, fit_mode: FitMode, resampling: Resampling, contrast: float, saturation: float
    ) -> None:
        self._fit_mode = fit_mode
        self._resampling = resampling
        self._contrast = contrast
        self._saturation = saturation

    def prepare(
        self, source: Path | bytes, *, width: int, height: int, pad_color: RgbTuple
    ) -> RgbImage:
        """Lädt, verbessert und passt das Bild ein; Ergebnis als NumPy-Array."""
        image = enhance(
            load_image(source, pad_color), contrast=self._contrast, saturation=self._saturation
        )
        fitted = fit_image(
            image,
            width=width,
            height=height,
            fit_mode=self._fit_mode,
            pad_color=pad_color,
            resampling=self._resampling,
        )
        # np.asarray macht aus dem Pillow-Bild ein (H, W, 3)-Array mit uint8-Werten
        return np.asarray(fitted, dtype=np.uint8)
