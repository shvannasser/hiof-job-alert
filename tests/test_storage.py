"""Vedvarende lagring og trygg oppdatering av varslingsstatus."""

from pathlib import Path

import pytest

import storage
from scraper import Job


def job(number: int, **fields) -> Job:
    return Job(
        id=f"jobbnorge:{number}",
        title=fields.pop("title", f"Stilling {number}"),
        url=f"https://www.jobbnorge.no/ledige-stillinger/stilling/{number}",
        source="jobbnorge",
        **fields,
    )


def test_missing_file_means_first_run(tmp_path):
    history = storage.load(tmp_path / "finnes-ikke.json")

    assert not history.initialized
    assert history.jobs == {}


def test_shipped_state_file_is_valid_and_waits_for_first_run():
    history = storage.load(Path(__file__).parent.parent / "data" / "seen_jobs.json")

    assert not history.initialized
    assert history.jobs == {}


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "data" / "seen_jobs.json"
    history = storage.History()
    history.register_baseline([job(1, published_date="2026-09-18")], "2026-10-12T13:00:00+00:00")
    history.add_or_update([job(2, title="Høgskolelektor i sykepleie")], ["discord", "ntfy"], "2026-10-13T13:00:00+00:00")

    storage.save(path, history)
    loaded = storage.load(path)

    assert loaded.data == history.data
    assert loaded.jobs["jobbnorge:1"]["baseline"] is True
    assert loaded.jobs["jobbnorge:1"]["notifications"] == {}
    assert loaded.pending("discord") == ["jobbnorge:2"]
    assert "Høgskolelektor i sykepleie" in path.read_text(encoding="utf-8")  # leselig UTF-8, ikke ø
    assert list(path.parent.glob("*.tmp")) == []


def test_save_is_atomic_when_writing_fails(tmp_path):
    path = tmp_path / "seen_jobs.json"
    storage.save(path, storage.History())
    before = path.read_bytes()
    broken = storage.History()
    broken.data["jobs"]["x"] = object()  # kan ikke skrives som JSON

    with pytest.raises(TypeError):
        storage.save(path, broken)

    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "content",
    [
        "{ ikke json",
        "[]",
        '{"schema_version": 99, "initialized_at": null, "jobs": {}}',
        '{"schema_version": 1, "jobs": []}',
    ],
)
def test_unreadable_file_is_never_overwritten(tmp_path, content):
    path = tmp_path / "seen_jobs.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(storage.StorageError):
        storage.load(path)

    assert path.read_text(encoding="utf-8") == content


def test_changed_job_is_updated_without_new_notification():
    history = storage.History()
    history.register_baseline([job(1, published_date="2026-09-18", department="IT")], "t0")

    new, changed = history.add_or_update([job(1, title="Stilling 1 (ny frist)")], ["discord", "ntfy"], "t1")

    record = history.jobs["jobbnorge:1"]
    assert (new, changed) == ([], ["jobbnorge:1"])
    assert record["title"] == "Stilling 1 (ny frist)"
    assert record["published_date"] == "2026-09-18"  # mangler i det nye treffet, men slettes ikke
    assert record["department"] == "IT"
    assert history.pending("discord") == []
    assert history.pending("ntfy") == []


def test_notification_status_is_tracked_separately_per_channel():
    history = storage.History()
    history.register_baseline([], "t0")
    history.add_or_update([job(1)], ["discord", "ntfy"], "t1")

    history.mark_sent("jobbnorge:1", "discord", "t2")
    status = history.mark_failed("jobbnorge:1", "ntfy", "HTTP 503 etter 3 forsøk", "t2")

    notifications = history.jobs["jobbnorge:1"]["notifications"]
    assert notifications["discord"] == {"status": "sent", "attempts": 1, "updated_at": "t2", "error": None}
    assert status == storage.PENDING
    assert notifications["ntfy"] == {"status": "pending", "attempts": 1, "updated_at": "t2", "error": "HTTP 503 etter 3 forsøk"}
    assert history.pending("discord") == []
    assert history.pending("ntfy") == ["jobbnorge:1"]


def test_gives_up_after_max_attempts():
    history = storage.History()
    history.register_baseline([], "t0")
    history.add_or_update([job(1)], ["ntfy"], "t1")

    statuses = [history.mark_failed("jobbnorge:1", "ntfy", "HTTP 500", "t") for _ in range(storage.MAX_ATTEMPTS)]

    assert statuses[-2] == storage.PENDING
    assert statuses[-1] == storage.FAILED
    assert history.pending("ntfy") == []
