"""Felles HTTP-oppsett: timeout, begrensede retries og feilmeldinger uten hemmeligheter."""

import logging
import time

import requests

log = logging.getLogger(__name__)

USER_AGENT = "hiof-job-alert/1.0 (privat stillingsvarsler; én kjøring per dag)"
TIMEOUT_SECONDS = 20
ATTEMPTS = 3
BACKOFF_SECONDS = 5  # ventetiden dobles for hvert nytt forsøk: 5 s, 10 s
MAX_RETRY_AFTER_SECONDS = 60
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class HttpError(Exception):
    """Forespørselen mislyktes.

    Meldingen inneholder aldri URL-en, fordi Discord-webhooken har tokenet sitt i URL-en.
    `transient` er True når feilen trolig er midlertidig (nettverk, 429, 5xx).
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """Utfører en forespørsel med timeout og opptil ATTEMPTS forsøk ved midlertidige feil."""
    for attempt in range(1, ATTEMPTS + 1):
        wait = BACKOFF_SECONDS * 2 ** (attempt - 1)
        try:
            response = session.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
        except requests.RequestException as exc:
            error = type(exc).__name__  # str(exc) kan inneholde URL-en
        else:
            if response.status_code < 400:
                return response
            error = f"HTTP {response.status_code}"
            if response.status_code not in TRANSIENT_STATUS_CODES:
                raise HttpError(error, transient=False)
            wait = _retry_after(response, default=wait)
        if attempt < ATTEMPTS:
            log.warning("Forsøk %d av %d feilet (%s). Prøver igjen om %.0f s.", attempt, ATTEMPTS, error, wait)
            time.sleep(wait)
    raise HttpError(f"{error} etter {ATTEMPTS} forsøk", transient=True)


def _retry_after(response: requests.Response, default: float) -> float:
    """Respekterer Retry-After (sekunder) fra f.eks. Discord ved 429, men venter aldri lenger enn maks."""
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return default
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)
