"""Daglig sjekk av ledige stillinger ved Høgskolen i Østfold.

    python src/main.py                      vanlig kjøring: hent, varsle, lagre, send daglig statusmelding
    python src/main.py --dry-run            vis hva som ville skjedd; sender og lagrer ingenting
    python src/main.py --test-notification  send en testmelding til Discord og ntfy

Avslutningskoder: 0 = alt OK, 1 = en kilde eller et varsel feilet (det som lyktes er lagret),
2 = konfigurasjons- eller lagringsfeil (ingenting er gjort).
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import http_client
import notifier
import scraper
import storage

log = logging.getLogger("hiof_job_alert")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "data" / "seen_jobs.json"
ENV_PATH = PROJECT_ROOT / ".env"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def describe_error(exc: Exception) -> str:
    """Kort feilbeskrivelse uten URL-er, så webhook-token aldri havner i logger eller historikk."""
    return str(exc) if isinstance(exc, http_client.HttpError) else type(exc).__name__


def load_dotenv(path: Path, environ=os.environ) -> None:
    """Leser NØKKEL=verdi fra .env ved lokal kjøring. Miljøvariabler som allerede finnes, vinner."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() and not key.lstrip().startswith("#"):
            environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_notifiers(env, session) -> list:
    """Bruker kanalene som er satt opp. Minst én av DISCORD_WEBHOOK_URL og NTFY_TOPIC må finnes."""
    discord_webhook_url = env.get("DISCORD_WEBHOOK_URL", "").strip()
    ntfy_topic = env.get("NTFY_TOPIC", "").strip()
    notifiers = []
    if discord_webhook_url:
        notifiers.append(notifier.DiscordNotifier(discord_webhook_url, session))
    if ntfy_topic:
        notifiers.append(notifier.NtfyNotifier(ntfy_topic, session))
    if not notifiers:
        raise notifier.ConfigError(
            "Mangler DISCORD_WEBHOOK_URL og NTFY_TOPIC. Legg inn minst én av dem som GitHub-secret (eller i .env lokalt)."
        )
    return notifiers


def check_jobs(state_path: Path, session, notifiers: list, *, dry_run: bool = False) -> int:
    history = storage.load(state_path)  # en skadet fil gir feil i stedet for å bli overskrevet
    jobs, failed_sources = scraper.fetch_all_jobs(session)
    if not history.initialized:
        return register_first_run(state_path, history, jobs, failed_sources, dry_run=dry_run)

    new_ids, changed_ids = history.add_or_update(jobs, [channel.name for channel in notifiers], utc_now())
    for job_id in new_ids:
        log.info("Ny stilling: %s – %s", job_id, history.jobs[job_id]["title"])
    for job_id in changed_ids:
        log.info("Endret stilling (varsles ikke på nytt): %s – %s", job_id, history.jobs[job_id]["title"])

    if dry_run:
        log.info("Tørrkjøring: %d nye stillinger ville blitt varslet. Ingenting er sendt eller lagret.", len(new_ids))
        return 1 if failed_sources else 0

    storage.save(state_path, history)  # nye annonser lagres som «pending» før noe sendes
    delivered = deliver_pending(history, notifiers, save=lambda: storage.save(state_path, history))
    log.info(
        "Ferdig: %d stillinger hentet, %d nye, %d endret. Kilder med feil: %s. Alle varsler levert: %s.",
        len(jobs), len(new_ids), len(changed_ids), ", ".join(failed_sources) or "ingen", "ja" if delivered else "nei",
    )
    return 0 if delivered and not failed_sources else 1


def register_first_run(state_path: Path, history: storage.History, jobs, failed_sources, *, dry_run: bool) -> int:
    """Første kjøring: eksisterende stillinger registreres uten varsel. Krever at alle kilder svarte."""
    if failed_sources:
        log.error("Første kjøring er avbrutt fordi en kilde feilet. Ingenting er lagret; kjør på nytt senere.")
        return 1
    history.register_baseline(jobs, utc_now())
    for job in jobs:
        log.info("Registrert uten varsel: %s – %s", job.id, job.title)
    if dry_run:
        log.info("Tørrkjøring: første kjøring ville registrert %d stillinger. Ingenting er lagret.", len(jobs))
        return 0
    storage.save(state_path, history)
    log.info("Første kjøring: %d eksisterende stillinger er registrert uten varsel. "
             "Nye stillinger varsles fra neste kjøring.", len(jobs))
    return 0


def deliver_pending(history: storage.History, notifiers: list, save) -> bool:
    """Sender ventende varsler kanal for kanal og lagrer status etter hvert enkelt varsel.

    En kanal som feiler, påvirker aldri den andre: statusen spores separat per kanal.
    """
    all_delivered = True
    for channel in notifiers:  # hver kanal behandles, også når en annen har feilet
        if not deliver_channel(history, channel, save):
            all_delivered = False
    return all_delivered


def deliver_channel(history: storage.History, channel, save) -> bool:
    """Sender én kanals ventende varsler. Returnerer False hvis minst ett feilet."""
    all_delivered = True
    pending = history.pending(channel.name)
    for index, job_id in enumerate(pending):
        record = history.jobs[job_id]
        try:
            channel.send(record)
        except Exception as exc:  # én feil skal aldri stoppe de andre varslene
            all_delivered = False
            reason = describe_error(exc)
            status = history.mark_failed(job_id, channel.name, reason, utc_now())
            save()
            outcome = "Gir opp etter for mange forsøk." if status == storage.FAILED else "Prøves igjen neste kjøring."
            # Bevisst ikke log.exception: sporingen kan inneholde webhook-URL-en.
            log.error("%s: varsel om %s feilet (%s). %s", channel.name, job_id, reason, outcome)  # NOSONAR
            remaining = len(pending) - index - 1
            if getattr(exc, "transient", False) and remaining:
                log.warning("%s svarer ikke nå; %d varsler til venter til neste kjøring.", channel.name, remaining)
                break
        else:
            history.mark_sent(job_id, channel.name, utc_now())
            save()
            log.info("%s: varslet om %s – %s", channel.name, job_id, record["title"])
    return all_delivered


def send_status(state_path: Path, notifiers: list, ok: bool, env=os.environ) -> None:
    """Kort statusmelding etter hver kjøring: «alt OK» eller «noe feilet».

    Kommer det ingen statusmelding etter kl. 12:07 eller 14:07, har sjekken ikke kjørt.
    """
    if ok:
        job_count = len(storage.load(state_path).jobs)
        text = (f"Sjekken er ferdig og alt virker. Følger med på {job_count} stillinger; nye varsles i egne meldinger.\n"
                "Kommer det ingen statusmelding etter neste sjekk (kl. 12:07 og 14:07), har den ikke kjørt.")
    else:
        text = ("Noe feilet i siste sjekk: en kilde svarte ikke, eller et varsel ble ikke levert. "
                "Det prøves igjen ved neste kjøring.")
    if env.get("GITHUB_RUN_ID"):  # satt automatisk i GitHub Actions
        text += f"\nLogg: {env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"

    for channel in notifiers:
        try:
            channel.send_status(text, ok)
        except Exception as exc:
            # Bevisst ikke log.exception: sporingen kan inneholde webhook-URL-en.
            log.error("%s: statusmeldingen feilet (%s).", channel.name, describe_error(exc))  # NOSONAR
        else:
            log.info("%s: statusmelding sendt.", channel.name)


def send_test_notification(notifiers: list) -> int:
    """Sender en tydelig merket testmelding. Historikken leses og endres ikke."""
    record = {
        "title": "TEST – dette er en testmelding, ikke en ny stilling",
        "url": scraper.HIOF_PAGE_URL,
        "published_date": None,
        "published_time": None,
        "department": None,
        "deadline": None,
    }
    all_sent = True
    for channel in notifiers:
        try:
            channel.send(record)
        except Exception as exc:
            all_sent = False
            # Bevisst ikke log.exception: sporingen kan inneholde webhook-URL-en.
            log.error("%s: testmeldingen feilet (%s).", channel.name, describe_error(exc))  # NOSONAR
        else:
            log.info("%s: testmelding sendt.", channel.name)
    return 0 if all_sent else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Varsler om nye stillinger ved Høgskolen i Østfold.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="hent og vis resultatet uten å sende varsler eller lagre")
    mode.add_argument("--test-notification", action="store_true", help="send en testmelding til Discord og ntfy")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.ERROR)  # urllib3 kan logge URL-er med token på lavere nivåer
    load_dotenv(ENV_PATH)
    session = http_client.create_session()
    try:
        notifiers = [] if args.dry_run else build_notifiers(os.environ, session)
        if args.test_notification:
            return send_test_notification(notifiers)
        exit_code = check_jobs(STATE_PATH, session, notifiers, dry_run=args.dry_run)
        if not args.dry_run:
            send_status(STATE_PATH, notifiers, ok=exit_code == 0)
        return exit_code
    except (notifier.ConfigError, storage.StorageError) as exc:
        # Forventede feil med egen, tydelig melding; sporingen trengs ikke.
        log.error("%s", exc)  # NOSONAR
        return 2


if __name__ == "__main__":
    sys.exit(main())
