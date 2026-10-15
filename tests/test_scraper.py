"""Parsing av annonser, stabil identifikasjon og feil fra nettsiden (ekte testdata, ingen nettverk)."""

import json

import pytest
import requests

import http_client
import scraper
from helpers import FakeSession, fixture, jobbnorge_job, json_response, response

FADDER_URL = "https://www.hiof.no/om/jobb/jobber-for-studenter/medlemmer-til-fadderstyret"
AMBASSADOR_URL = "https://www.hiof.no/om/jobb/jobber-for-studenter/vil-du-bli-studentambassador.html"


# --- Parsing ---------------------------------------------------------------------------------


def test_parse_hiof_page_finds_all_listed_jobs():
    jobs = scraper.parse_hiof_page(fixture("hiof_jobb.html").decode("utf-8"))

    assert [(job.id, job.title) for job in jobs] == [
        ("jobbnorge:307772", "Ledig stilling som kommunikasjonssjef"),
        (FADDER_URL, "Bli med i Fadderstyret 2027"),
        (AMBASSADOR_URL, "Vil du bli studentambassadør?"),
    ]
    assert jobs[0].url == "https://www.jobbnorge.no/ledige-stillinger/stilling/307772"
    assert all(job.published_date is None for job in jobs)  # listene på siden viser ingen datoer


def test_parse_hiof_page_rejects_unexpected_html():
    with pytest.raises(scraper.SourceError):
        scraper.parse_hiof_page("<html><body><h1>Siden er under vedlikehold</h1></body></html>")
    with pytest.raises(scraper.SourceError):
        scraper.parse_hiof_page("")


def test_parse_hiof_page_accepts_empty_lists():
    assert scraper.parse_hiof_page('<div class="vrtx-feed"></div><div class="vrtx-feed"><ul class="items"></ul></div>') == []


def test_parse_jobbnorge_reads_all_fields():
    [job] = scraper.parse_jobbnorge(json.loads(fixture("jobbnorge.json")))

    assert job == scraper.Job(
        id="jobbnorge:307772",
        title="Ledig stilling som kommunikasjonssjef",
        url="https://www.jobbnorge.no/ledige-stillinger/stilling/307772",
        source="jobbnorge",
        published_date="2026-09-18",
        published_time=None,  # API-et oppgir bare dato
        department="Organisasjonsavdelingen, Halden",
        deadline="18.10.2026",
    )


def test_parse_jobbnorge_never_invents_missing_metadata():
    data = {
        "jobs": [
            {"id": 1, "title": "  Rådgiver \n i HR ", "publicationDate": None, "deadline": "Løpende"},
            {"id": 2, "title": "Stipendiat", "publicationDate": "2026-10-01T10:00"},  # ukjent format
        ]
    }
    first, second = scraper.parse_jobbnorge(data)

    assert first.title == "Rådgiver i HR"
    assert first.published_date is None
    assert first.published_time is None
    assert first.department is None
    assert first.deadline == "Løpende"
    assert second.published_date is None


def test_parse_jobbnorge_skips_items_without_id_and_rejects_bad_structure():
    assert scraper.parse_jobbnorge({"jobs": [{"title": "Uten ID"}, "tull"]}) == []
    for bad in (None, [], {"items": []}, {"jobs": "ingen"}):
        with pytest.raises(scraper.SourceError):
            scraper.parse_jobbnorge(bad)


def test_parse_student_feed_gives_norwegian_publication_time():
    fadder, ambassador = scraper.parse_student_feed(fixture("studentstillinger.xml"))

    assert (fadder.id, fadder.title, fadder.url) == (FADDER_URL, "Bli med i Fadderstyret 2027", FADDER_URL)
    # 2026-10-06T09:29:40Z er kl. 11:29 norsk sommertid, som «Publisert» på selve annonsesiden.
    assert (fadder.published_date, fadder.published_time) == ("2026-10-06", "11:29")
    assert (ambassador.published_date, ambassador.published_time) == ("2025-08-14", "10:05")


def test_parse_timestamp_handles_winter_time_and_never_guesses():
    assert scraper.parse_timestamp("2026-01-15T10:00:00Z").strftime("%H:%M") == "11:00"
    assert scraper.parse_timestamp("2026-01-15T10:00:00") is None  # uten tidssone
    assert scraper.parse_timestamp("i går") is None
    assert scraper.parse_timestamp(None) is None


def test_parse_student_feed_rejects_invalid_content():
    with pytest.raises(scraper.SourceError):
        scraper.parse_student_feed(b"")
    with pytest.raises(scraper.SourceError):
        scraper.parse_student_feed(b"<html><body>Feil</body></html>")


# --- Stabil identifikasjon -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.jobbnorge.no/ledige-stillinger/stilling/307772",
        "https://www.jobbnorge.no/ledige-stillinger/stilling/307772/ledig-stilling-som-kommunikasjonssjef",
        "http://jobbnorge.no/ledige-stillinger/stilling/307772?utm_source=hiof",
    ],
)
def test_jobbnorge_ads_are_identified_by_job_number(url):
    assert scraper.identify(url) == ("jobbnorge:307772", "https://www.jobbnorge.no/ledige-stillinger/stilling/307772")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.hiof.no/om/jobb/jobber-for-studenter/foo",
        "https://www.hiof.no/om/jobb/jobber-for-studenter/foo/",
        "https://www.hiof.no/om/jobb/jobber-for-studenter/foo/index.html",
        "http://WWW.HIOF.NO/om/jobb/jobber-for-studenter/foo#soknad",
    ],
)
def test_other_ads_are_identified_by_normalized_url(url):
    assert scraper.identify(url)[0] == "https://www.hiof.no/om/jobb/jobber-for-studenter/foo"


def test_same_job_from_several_sources_is_merged(session):
    jobs, failed = scraper.fetch_all_jobs(session)

    assert failed == []
    by_id = {job.id: job for job in jobs}
    assert sorted(by_id) == sorted(["jobbnorge:307772", FADDER_URL, AMBASSADOR_URL])  # 6 treff, 3 unike
    assert by_id["jobbnorge:307772"].source == "jobbnorge"
    assert by_id["jobbnorge:307772"].published_date == "2026-09-18"
    assert by_id[FADDER_URL].published_time == "11:29"
    [api_call] = session.calls_to(scraper.JOBBNORGE_API_URL)
    assert api_call["params"] == {"employer": 1562}
    assert api_call["timeout"] == http_client.TIMEOUT_SECONDS


def test_duplicate_within_one_source_gives_one_job(session):
    job = jobbnorge_job(400001, "Professor")
    session.set(scraper.JOBBNORGE_API_URL, json_response({"jobs": [job, job]}))

    jobs, _ = scraper.fetch_all_jobs(session)

    assert [found.id for found in jobs].count("jobbnorge:400001") == 1


# --- Feil fra nettsiden ----------------------------------------------------------------------


def test_other_sources_are_used_when_one_fails(session):
    session.set(scraper.JOBBNORGE_API_URL, 500)

    jobs, failed = scraper.fetch_all_jobs(session)

    assert failed == ["Jobbnorge-API"]
    job = {found.id: found for found in jobs}["jobbnorge:307772"]
    assert job.source == "hiof-jobbside"  # funnet via HiØ-siden, men uten dato fra API-et
    assert job.published_date is None
    assert len(session.calls_to(scraper.JOBBNORGE_API_URL)) == http_client.ATTEMPTS


def test_invalid_json_from_api_is_a_source_error(session):
    session.set(scraper.JOBBNORGE_API_URL, response(200, "<html>ikke json</html>"))

    _, failed = scraper.fetch_all_jobs(session)

    assert failed == ["Jobbnorge-API"]


def test_temporary_errors_are_retried_with_increasing_wait(sleeps):
    session = FakeSession()
    session.set(scraper.HIOF_PAGE_URL, 503, requests.ConnectionError("nede"), response(200, "ok"))

    assert http_client.request(session, "GET", scraper.HIOF_PAGE_URL).text == "ok"
    assert sleeps == [5, 10]


def test_403_is_not_retried(sleeps):
    session = FakeSession()
    session.set(scraper.HIOF_PAGE_URL, 403)

    with pytest.raises(http_client.HttpError) as error:
        http_client.request(session, "GET", scraper.HIOF_PAGE_URL)

    assert str(error.value) == "HTTP 403"
    assert not error.value.transient
    assert len(session.calls) == 1
    assert sleeps == []


def test_retries_are_limited():
    session = FakeSession()
    session.set(scraper.HIOF_PAGE_URL, requests.Timeout("for treg"))

    with pytest.raises(http_client.HttpError) as error:
        http_client.request(session, "GET", scraper.HIOF_PAGE_URL)

    assert str(error.value) == "Timeout etter 3 forsøk"
    assert error.value.transient
    assert len(session.calls) == http_client.ATTEMPTS


def test_retry_after_is_respected_but_capped(sleeps):
    session = FakeSession()
    session.set(
        scraper.HIOF_PAGE_URL,
        response(429, headers={"Retry-After": "2"}),
        response(429, headers={"Retry-After": "3600"}),
        200,
    )

    http_client.request(session, "GET", scraper.HIOF_PAGE_URL)

    assert sleeps == [2.0, http_client.MAX_RETRY_AFTER_SECONDS]
