"""OAuth 1.0a (RFC 5849) mit HMAC-SHA1, wie es die BrickLink-API verlangt.

Nur Standardbibliothek: Für eine signierte GET-Anfrage braucht es keine eigene
Abhängigkeit. Nonce und Zeitstempel lassen sich für Tests fest vorgeben.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from urllib.parse import quote

from lego_mosaik.pricing.credentials import Credentials

SIGNATURE_METHOD = "HMAC-SHA1"
OAUTH_VERSION = "1.0"
UNRESERVED = "-._~"  # Zeichen, die laut RFC 3986 nie kodiert werden (dazu Buchstaben, Ziffern)


def authorization_header(
    method: str,
    url: str,
    params: Mapping[str, str],
    credentials: Credentials,
    *,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> str:
    """Wert des ``Authorization``-Headers für eine Anfrage.

    Args:
        method: HTTP-Methode, z. B. ``"GET"``.
        url: URL ohne Query-String.
        params: Query-Parameter der Anfrage (fließen in die Signatur ein).
        credentials: Zugangsdaten.
        nonce: Einmalwert; ``None`` = zufällig.
        timestamp: Sekunden seit 1970; ``None`` = jetzt.
    """
    oauth = {
        "oauth_consumer_key": credentials.consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": SIGNATURE_METHOD,
        "oauth_timestamp": str(int(time.time()) if timestamp is None else timestamp),
        "oauth_token": credentials.token,
        "oauth_version": OAUTH_VERSION,
    }
    oauth["oauth_signature"] = signature(method, url, {**params, **oauth}, credentials)
    fields = ", ".join(f'{key}="{_encode(value)}"' for key, value in sorted(oauth.items()))
    return f"OAuth {fields}"


def signature(method: str, url: str, params: Mapping[str, str], credentials: Credentials) -> str:
    """HMAC-SHA1-Signatur über Methode, URL und sortierte Parameter (RFC 5849, 3.4)."""
    pairs = sorted((_encode(key), _encode(value)) for key, value in params.items())
    normalized = "&".join(f"{key}={value}" for key, value in pairs)
    base = "&".join((method.upper(), _encode(url), _encode(normalized)))
    key = f"{_encode(credentials.consumer_secret)}&{_encode(credentials.token_secret)}"
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _encode(value: str) -> str:
    """Prozentkodierung laut RFC 5849, 3.6 (auch „/“ und Leerzeichen werden kodiert)."""
    return quote(value, safe=UNRESERVED)
