"""Hele kjøringen: første kjøring, nye annonser, duplikater, feil i hver kanal og trygg lagring."""

import json
import logging

import pytest
import requests

import http_client
import main
import notifier
import scraper
import storage
from helpers import DISCORD_URL, NTFY_TOPIC, fixture, jobbnorge_job, json_response, response

REAL_API_JOB = json.loads(fixture("jobbnorge.json"))["jobs"][0]


def api_with(*extra_jobs) -> requests.Response:
    return json_response({"jobs": [REAL_API_JOB, *extra_jobs]})


def run_baseline(state_path, session, notifiers) -> None:
    """Første kjøring med dagens ekte data, deretter blanke ark for forespørslene."""
    assert main.check_jobs(state_path, session, notifiers) == 0
    session.calls.clear()


def statuses(state_path, job_id) -> dict:
    notifications = storage.load(state_path).jobs[job_id]["notifications"]
    return {channel: status["status"] for channel, status in notifications.items()}


def assert_nothing_sent(session) -> None:
    assert session.calls_to(DISCORD_URL) == []
    assert session.calls_to(notifier.NTFY_SERVER) == []


# --- Første kjøring --------------------------------------------------------------------------


def test_first_run_registers_existing_jobs_without_notifying(state_path, session, notifiers):
    assert main.check_jobs(state_path, session, notifiers) == 0

    history = storage.load(state_path)
    assert history.initialized
    assert len(history.jobs) == 3
    assert all(record["baseline"] and record["notifications"] == {} for record in history.jobs.values())
    assert_nothing_sent(session)


def test_first_run_saves_nothing_if_a_source_fails(state_path, session, notifiers):
    session.set(scraper.STUDENT_FEED_URL, 503)

    assert main.check_jobs(state_path, session, notifiers) == 1
    assert not state_path.exists()


# --- Nye annonser og duplikater --------------------------------------------------------------


def test_new_job_is_notified_once_in_each_channel(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "Førsteamanuensis i informatikk")))

    assert main.check_jobs(state_path, session, notifiers) == 0

    [discord] = session.calls_to(DISCORD_URL)
    [ntfy] = session.calls_to(notifier.NTFY_SERVER)
    assert "Tittel: Førsteamanuensis i informatikk" in discord["json"]["content"]
    assert "Publisert: 01.10.2026 (klokkeslett ikke oppgitt)" in ntfy["json"]["message"]
    assert statuses(state_path, "jobbnorge:400001") == {"discord": "sent", "ntfy": "sent"}
    assert storage.load(state_path).jobs["jobbnorge:400001"]["baseline"] is False


def test_no_duplicate_notifications_on_later_runs(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "Professor")))

    for _ in range(3):
        assert main.check_jobs(state_path, session, notifiers) == 0

    assert len(session.calls_to(DISCORD_URL)) == 1
    assert len(session.calls_to(notifier.NTFY_SERVER)) == 1


def test_empty_sources_never_delete_history_and_returning_jobs_are_not_renotified(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    real_routes = dict(session.routes)
    session.set(scraper.JOBBNORGE_API_URL, json_response({"jobs": []}))
    session.set(scraper.STUDENT_FEED_URL, response(200, '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'))
    session.set(scraper.HIOF_PAGE_URL, response(200, '<div class="vrtx-feed"></div>'))

    assert main.check_jobs(state_path, session, notifiers) == 0
    assert len(storage.load(state_path).jobs) == 3

    session.routes.update(real_routes)  # annonsene dukker opp igjen
    assert main.check_jobs(state_path, session, notifiers) == 0
    assert_nothing_sent(session)


def test_changed_job_is_updated_but_not_notified_again(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, json_response({"jobs": [{**REAL_API_JOB, "deadline": "25.10.2026"}]}))

    assert main.check_jobs(state_path, session, notifiers) == 0

    assert storage.load(state_path).jobs["jobbnorge:307772"]["deadline"] == "25.10.2026"
    assert_nothing_sent(session)


def test_job_only_on_hiof_page_is_notified_with_missing_date(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    new_item = '<ul class="items"><li><a class="item-title" href="/om/jobb/ny-stilling.html">Ny stilling uten dato</a></li>'
    page = fixture("hiof_jobb.html").decode("utf-8").replace('<ul class="items">', new_item, 1)
    session.set(scraper.HIOF_PAGE_URL, response(200, page))

    assert main.check_jobs(state_path, session, notifiers) == 0

    [ntfy] = session.calls_to(notifier.NTFY_SERVER)
    assert "Publisert: ikke oppgitt av kilden" in ntfy["json"]["message"]
    assert ntfy["json"]["click"] == "https://www.hiof.no/om/jobb/ny-stilling.html"


# --- Feil fra nettsiden ----------------------------------------------------------------------


def test_website_errors_leave_history_untouched(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    before = state_path.read_bytes()
    session.set(scraper.JOBBNORGE_API_URL, 500)
    session.set(scraper.STUDENT_FEED_URL, requests.ConnectionError("nede"))
    session.set(scraper.HIOF_PAGE_URL, response(200, "<html><body>Uventet innhold</body></html>"))

    assert main.check_jobs(state_path, session, notifiers) == 1

    assert state_path.read_bytes() == before
    assert_nothing_sent(session)


def test_403_from_website_is_reported_without_hammering(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.HIOF_PAGE_URL, 403)

    assert main.check_jobs(state_path, session, notifiers) == 1
    assert len(session.calls_to(scraper.HIOF_PAGE_URL)) == 1


# --- Feil fra Discord og ntfy ----------------------------------------------------------------


def test_discord_failure_does_not_affect_ntfy_and_is_retried_next_run(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "Professor")))
    session.set(DISCORD_URL, 500)

    assert main.check_jobs(state_path, session, notifiers) == 1
    discord = storage.load(state_path).jobs["jobbnorge:400001"]["notifications"]["discord"]
    assert (discord["status"], discord["attempts"], discord["error"]) == ("pending", 1, "HTTP 500 etter 3 forsøk")
    assert statuses(state_path, "jobbnorge:400001")["ntfy"] == "sent"

    session.set(DISCORD_URL, 200)
    session.calls.clear()
    assert main.check_jobs(state_path, session, notifiers) == 0
    assert len(session.calls_to(DISCORD_URL)) == 1
    assert session.calls_to(notifier.NTFY_SERVER) == []  # ntfy fikk den allerede
    assert statuses(state_path, "jobbnorge:400001") == {"discord": "sent", "ntfy": "sent"}


def test_ntfy_failure_does_not_affect_discord_and_is_retried_next_run(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "Professor")))
    session.set(notifier.NTFY_SERVER, requests.ConnectionError("nede"))

    assert main.check_jobs(state_path, session, notifiers) == 1
    assert statuses(state_path, "jobbnorge:400001") == {"discord": "sent", "ntfy": "pending"}

    session.set(notifier.NTFY_SERVER, 200)
    session.calls.clear()
    assert main.check_jobs(state_path, session, notifiers) == 0
    assert session.calls_to(DISCORD_URL) == []  # Discord fikk den allerede
    assert len(session.calls_to(notifier.NTFY_SERVER)) == 1
    assert statuses(state_path, "jobbnorge:400001") == {"discord": "sent", "ntfy": "sent"}


def test_unavailable_channel_is_paused_for_the_rest_of_the_run(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "A"), jobbnorge_job(400002, "B")))
    session.set(DISCORD_URL, requests.ConnectionError("nede"))

    assert main.check_jobs(state_path, session, notifiers) == 1

    assert len(session.calls_to(DISCORD_URL)) == http_client.ATTEMPTS  # bare første varsel forsøkt
    assert len(session.calls_to(notifier.NTFY_SERVER)) == 2
    jobs = storage.load(state_path).jobs
    assert jobs["jobbnorge:400002"]["notifications"]["discord"] == {
        "status": "pending", "attempts": 0, "updated_at": None, "error": None,
    }


def test_rejected_message_does_not_block_the_next_one(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "A"), jobbnorge_job(400002, "B")))
    session.set(DISCORD_URL, 400, 200)

    assert main.check_jobs(state_path, session, notifiers) == 1

    assert statuses(state_path, "jobbnorge:400001")["discord"] == "pending"
    assert statuses(state_path, "jobbnorge:400002")["discord"] == "sent"


# --- Trygg oppdatering av varslingsstatus ----------------------------------------------------


def test_crash_mid_run_keeps_sent_status_and_avoids_duplicates(state_path, session, notifiers):
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "A"), jobbnorge_job(400002, "B")))

    class CrashingDiscord:
        name = "discord"
        sent = 0

        def send(self, record):
            if self.sent == 1:
                raise KeyboardInterrupt  # prosessen dør midt i kjøringen
            self.sent += 1

    with pytest.raises(KeyboardInterrupt):
        main.check_jobs(state_path, session, [CrashingDiscord(), notifiers[1]])

    assert statuses(state_path, "jobbnorge:400001") == {"discord": "sent", "ntfy": "pending"}
    assert statuses(state_path, "jobbnorge:400002") == {"discord": "pending", "ntfy": "pending"}

    session.calls.clear()
    assert main.check_jobs(state_path, session, notifiers) == 0
    [discord] = session.calls_to(DISCORD_URL)
    assert "Tittel: B" in discord["json"]["content"]  # A sendes ikke på nytt
    assert len(session.calls_to(notifier.NTFY_SERVER)) == 2


def test_secrets_never_reach_logs_or_history(state_path, session, notifiers, caplog):
    caplog.set_level(logging.DEBUG)
    run_baseline(state_path, session, notifiers)
    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "Professor")))
    session.set(DISCORD_URL, requests.ConnectionError(f"Max retries exceeded with url: {DISCORD_URL}?wait=true"))
    session.set(notifier.NTFY_SERVER, requests.ConnectionError(f"Kunne ikke publisere til {NTFY_TOPIC}"))

    assert main.check_jobs(state_path, session, notifiers) == 1

    saved = state_path.read_text(encoding="utf-8")
    assert "ConnectionError etter 3 forsøk" in saved
    for secret in ("HEMMELIG-TOKEN", NTFY_TOPIC):
        assert secret not in saved
        assert secret not in caplog.text


# --- Kommandolinje og konfigurasjon ----------------------------------------------------------


@pytest.fixture
def cli(monkeypatch, tmp_path, session):
    """Kjører main.main() mot den falske sesjonen og en midlertidig historikkfil."""
    monkeypatch.setattr(main.http_client, "create_session", lambda: session)
    monkeypatch.setattr(main, "STATE_PATH", tmp_path / "seen_jobs.json")
    monkeypatch.setattr(main, "ENV_PATH", tmp_path / "finnes-ikke.env")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DISCORD_URL)
    monkeypatch.setenv("NTFY_TOPIC", NTFY_TOPIC)
    return main.STATE_PATH


def test_missing_secrets_stop_before_any_request(cli, monkeypatch, session):
    monkeypatch.delenv("NTFY_TOPIC")

    assert main.main([]) == 2
    assert session.calls == []


def test_dry_run_sends_and_saves_nothing(cli, monkeypatch, session):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL")  # tørrkjøring trenger ingen secrets

    assert main.main(["--dry-run"]) == 0
    assert not cli.exists()
    assert_nothing_sent(session)


def test_test_notification_reaches_both_channels_without_touching_history(cli, session):
    assert main.main(["--test-notification"]) == 0

    [discord] = session.calls_to(DISCORD_URL)
    assert "TEST" in discord["json"]["content"]
    assert len(session.calls_to(notifier.NTFY_SERVER)) == 1
    assert session.calls_to(scraper.HIOF_PAGE_URL) == []
    assert not cli.exists()


def test_full_cli_run_first_registers_then_notifies(cli, session):
    assert main.main([]) == 0
    assert storage.load(cli).initialized

    session.set(scraper.JOBBNORGE_API_URL, api_with(jobbnorge_job(400001, "Rektor")))
    assert main.main([]) == 0
    assert len(session.calls_to(DISCORD_URL)) == 1


def test_corrupt_history_stops_the_run_without_overwriting(cli, session):
    cli.write_text("{ skadet", encoding="utf-8")

    assert main.main([]) == 2
    assert cli.read_text(encoding="utf-8") == "{ skadet"
    assert session.calls_to(DISCORD_URL) == []


def test_dotenv_is_read_but_real_environment_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# kommentar\nNTFY_TOPIC="fra-fil"\nDISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/fil\n',
        encoding="utf-8",
    )
    environ = {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/miljo"}

    main.load_dotenv(env_file, environ)

    assert environ == {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/miljo", "NTFY_TOPIC": "fra-fil"}
