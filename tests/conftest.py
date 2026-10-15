import pytest

import http_client
import notifier
import scraper
from helpers import DISCORD_URL, NTFY_TOPIC, FakeSession, fixture, response


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list:
    """Retries venter ikke i testene; ventetidene registreres i stedet."""
    recorded = []
    monkeypatch.setattr(http_client.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def session() -> FakeSession:
    """Kildene svarer slik de faktisk gjorde 12.10.2026, og begge varslingskanalene fungerer."""
    fake = FakeSession()
    fake.set(scraper.JOBBNORGE_API_URL, response(200, fixture("jobbnorge.json"), {"Content-Type": "application/json"}))
    fake.set(scraper.STUDENT_FEED_URL, response(200, fixture("studentstillinger.xml")))
    fake.set(scraper.HIOF_PAGE_URL, response(200, fixture("hiof_jobb.html")))
    fake.set(DISCORD_URL, 200)
    fake.set(notifier.NTFY_SERVER, 200)
    return fake


@pytest.fixture
def notifiers(session) -> list:
    return [notifier.DiscordNotifier(DISCORD_URL, session), notifier.NtfyNotifier(NTFY_TOPIC, session)]


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "data" / "seen_jobs.json"
