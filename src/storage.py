"""Historikk over oppdagede annonser og varslingsstatus per kanal, lagret i én JSON-fil.

Historikken slettes aldri automatisk: annonser som forsvinner fra kildene blir liggende,
slik at feil, tomme svar eller en annonse som dukker opp igjen aldri gir nye varsler.
"""

import json
import os
import tempfile
from pathlib import Path

from scraper import Job

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 10  # antall kjøringer et varsel prøves før vi gir opp
PENDING, SENT, FAILED = "pending", "sent", "failed"
STORED_FIELDS = ("title", "url", "source", "published_date", "published_time", "department", "deadline")
UPDATABLE_FIELDS = ("title", "url", "published_date", "published_time", "department", "deadline")


class StorageError(Exception):
    """Historikkfilen finnes, men kan ikke leses trygt. Filen blir ikke endret."""


class History:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {"schema_version": SCHEMA_VERSION, "initialized_at": None, "jobs": {}}

    @property
    def initialized(self) -> bool:
        return self.data["initialized_at"] is not None

    @property
    def jobs(self) -> dict[str, dict]:
        return self.data["jobs"]

    def register_baseline(self, jobs: list[Job], now: str) -> None:
        """Første kjøring: registrerer eksisterende annonser uten å varsle om dem."""
        for job in jobs:
            self.jobs.setdefault(job.id, _new_record(job, now, channels=(), baseline=True))
        self.data["initialized_at"] = now

    def add_or_update(self, jobs: list[Job], channels: list[str], now: str) -> tuple[list[str], list[str]]:
        """Nye annonser får status «pending» i hver kanal. Kjente annonser oppdateres uten nytt varsel.

        Returnerer (ID-er for nye annonser, ID-er for endrede annonser).
        """
        new, changed = [], []
        for job in jobs:
            record = self.jobs.get(job.id)
            if record is None:
                self.jobs[job.id] = _new_record(job, now, channels, baseline=False)
                new.append(job.id)
                continue
            # Bare felter kilden faktisk oppgir, så en kilde med mindre data aldri sletter kjente opplysninger.
            updates = {
                field: getattr(job, field)
                for field in UPDATABLE_FIELDS
                if getattr(job, field) is not None and record.get(field) != getattr(job, field)
            }
            if updates:
                record.update(updates)
                changed.append(job.id)
        return new, changed

    def pending(self, channel: str) -> list[str]:
        return [
            job_id
            for job_id, record in self.jobs.items()
            if record["notifications"].get(channel, {}).get("status") == PENDING
        ]

    def mark_sent(self, job_id: str, channel: str, now: str) -> None:
        status = self.jobs[job_id]["notifications"][channel]
        status.update(status=SENT, attempts=status["attempts"] + 1, updated_at=now, error=None)

    def mark_failed(self, job_id: str, channel: str, error: str, now: str) -> str:
        """Teller opp forsøk. Varselet prøves igjen neste kjøring til MAX_ATTEMPTS er nådd."""
        status = self.jobs[job_id]["notifications"][channel]
        attempts = status["attempts"] + 1
        status.update(
            status=FAILED if attempts >= MAX_ATTEMPTS else PENDING,
            attempts=attempts,
            updated_at=now,
            error=error,
        )
        return status["status"]


def _new_record(job: Job, now: str, channels, baseline: bool) -> dict:
    record = {field: getattr(job, field) for field in STORED_FIELDS}
    record["first_seen_at"] = now  # når vi oppdaget annonsen, ikke når den ble publisert
    record["baseline"] = baseline
    record["notifications"] = {
        channel: {"status": PENDING, "attempts": 0, "updated_at": None, "error": None} for channel in channels
    }
    return record


def load(path: Path) -> History:
    """Leser historikken. Mangler filen, starter vi på nytt med en første kjøring."""
    if not path.exists():
        return History()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StorageError(f"Kan ikke lese historikken i {path}: {exc}. Filen er ikke endret.") from exc
    valid = (
        isinstance(data, dict)
        and data.get("schema_version") == SCHEMA_VERSION
        and "initialized_at" in data
        and isinstance(data.get("jobs"), dict)
    )
    if not valid:
        raise StorageError(f"Historikken i {path} har et ukjent format. Filen er ikke endret.")
    return History(data)


def save(path: Path, history: History) -> None:
    """Skriver atomisk: enten lagres hele den nye filen, eller så står den gamle urørt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            json.dump(history.data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
