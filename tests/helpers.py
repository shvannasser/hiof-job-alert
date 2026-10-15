"""Hjelpere for testene: falske HTTP-svar slik at ingen test kontakter HiØ, Jobbnorge, Discord eller ntfy."""

import json
from pathlib import Path

import requests

FIXTURES = Path(__file__).parent / "fixtures"
DISCORD_URL = "https://discord.com/api/webhooks/123456/HEMMELIG-TOKEN"
NTFY_TOPIC = "hemmelig-emne-abc123"


def fixture(name: str) -> bytes:
    """Ekte svar fra kildene, lagret 12.10.2026."""
    return (FIXTURES / name).read_bytes()


def response(status: int = 200, body: bytes | str = b"", headers: dict | None = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8") if isinstance(body, str) else body
    resp.headers.update(headers or {})
    resp.encoding = "utf-8"
    return resp


def json_response(data) -> requests.Response:
    return response(200, json.dumps(data), {"Content-Type": "application/json"})


def jobbnorge_job(number: int, title: str, published: str = "01.10.2026") -> dict:
    """En annonse i samme format som Jobbnorge-API-et returnerer."""
    return {
        "id": number,
        "title": title,
        "link": f"https://www.jobbnorge.no/ledige-stillinger/stilling/{number}",
        "publicationDate": published,
        "deadline": "01.11.2026",
        "department": "Fakultet for informasjonsteknologi",
        "locations": [{"area": "Halden", "municipality": "Halden", "isPrimary": True}],
    }


class FakeSession:
    """Erstatter requests.Session og svarer fra en tabell.

    Hver URL har en liste med utfall som brukes etter tur; det siste gjentas.
    Et utfall er et requests.Response, en HTTP-statuskode eller et unntak som kastes.
    """

    def __init__(self) -> None:
        self.routes: dict[str, list] = {}
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}

    def set(self, url: str, *outcomes) -> None:
        self.routes[url] = list(outcomes)

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        outcomes = self.routes.get(url)
        if outcomes is None:
            raise AssertionError(f"Uventet forespørsel: {method} {url}")
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, int):
            return response(outcome)
        return outcome

    def calls_to(self, url: str) -> list[dict]:
        return [call for call in self.calls if call["url"] == url]
