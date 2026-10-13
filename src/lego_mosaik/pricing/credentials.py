"""BrickLink-Zugangsdaten aus Umgebungsvariablen.

Die vier Werte gibt es nach der Registrierung unter
https://www.bricklink.com/v2/api/register_consumer.page. Sie stehen bewusst nicht in
``CONFIG``: So landen sie weder in ``ergebnis.json`` noch in Git.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from lego_mosaik.domain.errors import MissingCredentialsError

ENV_NAMES = (
    "BRICKLINK_CONSUMER_KEY",
    "BRICKLINK_CONSUMER_SECRET",
    "BRICKLINK_TOKEN_VALUE",
    "BRICKLINK_TOKEN_SECRET",
)


@dataclass(frozen=True, slots=True)
class Credentials:
    """Die vier Werte für OAuth 1.0a (Consumer und Access Token)."""

    consumer_key: str
    consumer_secret: str
    token: str
    token_secret: str

    def __repr__(self) -> str:
        # Geheimnisse dürfen nie in Logs oder Tracebacks auftauchen
        return "Credentials(***)"


def load_credentials(environ: Mapping[str, str] | None = None) -> Credentials:
    """Liest die Zugangsdaten aus der Umgebung.

    Raises:
        MissingCredentialsError: Wenn mindestens eine Variable fehlt oder leer ist.
    """
    env = os.environ if environ is None else environ
    values = [env.get(name, "").strip() for name in ENV_NAMES]
    missing = [name for name, value in zip(ENV_NAMES, values, strict=True) if not value]
    if missing:
        raise MissingCredentialsError(
            f"BrickLink-Zugangsdaten fehlen: {', '.join(missing)}. API-Zugang auf "
            "bricklink.com registrieren und die Umgebungsvariablen setzen (siehe README). "
            "Ohne Zugang: nur gespeicherte Preise mit „--offline“."
        )
    consumer_key, consumer_secret, token, token_secret = values
    return Credentials(consumer_key, consumer_secret, token, token_secret)
