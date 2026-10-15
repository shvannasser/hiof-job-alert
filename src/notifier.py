"""Formaterer og sender varsler til Discord (Incoming Webhook) og ntfy."""

import re
from datetime import date
from urllib.parse import urlsplit

import http_client

HEADER = "NY STILLING – Høgskolen i Østfold"
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")  # samme regel som ntfy bruker for emnenavn
MAX_TITLE_LENGTH = 250  # holder meldingen godt under grensene (Discord 2000 tegn, ntfy 4096 byte)


class ConfigError(Exception):
    """Manglende eller ugyldig konfigurasjon. Meldingen inneholder aldri selve verdien."""


def published_text(record: dict) -> str:
    """Faktisk publiseringstidspunkt fra kilden, aldri tidspunktet vi oppdaget annonsen."""
    if not record.get("published_date"):
        return "ikke oppgitt av kilden"
    day = date.fromisoformat(record["published_date"]).strftime("%d.%m.%Y")
    if record.get("published_time"):
        return f"{day} kl. {record['published_time']}"
    return f"{day} (klokkeslett ikke oppgitt)"


def message_lines(record: dict, escape=lambda text: text) -> list[str]:
    title = record["title"]
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 1] + "…"
    lines = [f"Tittel: {escape(title)}", f"Publisert: {published_text(record)}"]
    if record.get("department"):
        lines.append(f"Avdeling/sted: {escape(record['department'])}")
    if record.get("deadline"):
        lines.append(f"Søknadsfrist: {escape(record['deadline'])}")
    lines.append(f"Lenke: {record['url']}")
    return lines


def discord_escape(text: str) -> str:
    """Hindrer at tegn som * og _ i en tittel blir tolket som Discord-formatering."""
    return re.sub(r"([\\*_~`|\[\]])", r"\\\1", text)


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url: str, session) -> None:
        parts = urlsplit(webhook_url)
        if parts.scheme != "https" or "/api/webhooks/" not in parts.path:
            raise ConfigError("DISCORD_WEBHOOK_URL ser ikke ut som en Discord-webhook (https://discord.com/api/webhooks/...).")
        self._url = webhook_url
        self._session = session

    def send(self, record: dict) -> None:
        content = f"**{HEADER}**\n\n" + "\n".join(message_lines(record, escape=discord_escape))
        payload = {
            "content": content,
            "allowed_mentions": {"parse": []},  # en tittel med @everyone skal aldri pinge noen
        }
        # wait=true: Discord svarer først når meldingen faktisk er opprettet.
        http_client.request(self._session, "POST", self._url, params={"wait": "true"}, json=payload)


class NtfyNotifier:
    name = "ntfy"

    def __init__(self, topic: str, session) -> None:
        server, topic = NTFY_SERVER, topic.strip()
        if topic.startswith(("https://", "http://")):  # full URL til et emne, f.eks. på egen ntfy-server
            parts = urlsplit(topic)
            server, topic = f"{parts.scheme}://{parts.netloc}", parts.path.strip("/")
        if not NTFY_TOPIC.fullmatch(topic):
            raise ConfigError("NTFY_TOPIC må være et emnenavn med bare A–Z, a–z, 0–9, - og _ (maks 64 tegn).")
        self._server = server
        self._topic = topic
        self._session = session

    def send(self, record: dict) -> None:
        # JSON-publisering til rot-URL-en støtter UTF-8 (æøå) i både tittel og tekst.
        payload = {
            "topic": self._topic,
            "title": HEADER,
            "message": "\n".join(message_lines(record)),
            "tags": ["briefcase"],
            "click": record["url"],  # trykk på varselet åpner annonsen
            "actions": [{"action": "view", "label": "Åpne annonsen", "url": record["url"]}],
        }
        http_client.request(self._session, "POST", self._server, json=payload)
