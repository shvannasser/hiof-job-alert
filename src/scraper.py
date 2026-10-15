"""Henter stillinger fra HiØs jobbside og de strukturerte kildene siden viser.

Jobbsiden https://www.hiof.no/om/jobb/ er vanlig server-rendret HTML (Vortex) med to lister:
«Alle ledige stillinger hos HiØ i Jobbnorge» og «Ledige studentstillinger på HiØ».
Listene viser bare tittel og lenke, så vi henter også de strukturerte kildene bak dem:

1. Jobbnorge Public API v3: stabil stillings-ID, avdeling, sted og publiseringsdato (kun dato).
2. Vortex Atom-feed for studentstillinger: publiseringstidspunkt med klokkeslett.
3. Selve jobbsiden (HTML): fanger opp alt siden viser, også om en av kildene over endres.

Alle tre ble verifisert mot de faktiske svarene 12.10.2026.
"""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import http_client

log = logging.getLogger(__name__)

OSLO = ZoneInfo("Europe/Oslo")
HIOF_PAGE_URL = "https://www.hiof.no/om/jobb/"
STUDENT_FEED_URL = "https://www.hiof.no/om/jobb/jobber-for-studenter/?vrtx=feed"
JOBBNORGE_API_URL = "https://publicapi.jobbnorge.no/v3/Jobs"
JOBBNORGE_EMPLOYER_ID = 1562  # Høgskolen i Østfold hos Jobbnorge
JOBBNORGE_JOB_URL = "https://www.jobbnorge.no/ledige-stillinger/stilling/{}"
JOBBNORGE_LINK = re.compile(r"https?://(?:www\.)?jobbnorge\.no/ledige-stillinger/stilling/(\d+)", re.IGNORECASE)
ATOM = "{http://www.w3.org/2005/Atom}"
NO_TITLE = "(uten tittel)"


class SourceError(Exception):
    """En kilde svarte, men med tomt, ugyldig eller uventet innhold."""


@dataclass
class Job:
    id: str
    title: str
    url: str
    source: str
    published_date: str | None = None  # ÅÅÅÅ-MM-DD slik kilden oppgir den
    published_time: str | None = None  # TT:MM norsk tid, bare når kilden oppgir klokkeslett
    department: str | None = None  # avdeling og/eller arbeidssted
    deadline: str | None = None  # søknadsfrist ordrett fra kilden, f.eks. «18.10.2026» eller «Løpende»


def identify(url: str) -> tuple[str, str]:
    """Gir (stabil ID, lenke) for en annonse-URL.

    Jobbnorge-annonser identifiseres med stillingsnummeret, slik at lenker med og uten
    tittel i adressen regnes som samme annonse. Andre annonser identifiseres med normalisert URL.
    """
    match = JOBBNORGE_LINK.match(url.strip())
    if match:
        number = match.group(1)
        return f"jobbnorge:{number}", JOBBNORGE_JOB_URL.format(number)
    return normalize_url(url), url.strip()


def normalize_url(url: str) -> str:
    """Fjerner variasjoner som ikke endrer hvilken side det er: http/https, index.html, / til slutt, #anker."""
    parts = urlsplit(url.strip())
    path = re.sub(r"/index\.html?$", "/", parts.path).rstrip("/") or "/"
    return urlunsplit(("https", parts.netloc.lower(), path, parts.query, ""))


def clean(value: object) -> str | None:
    """Slår sammen mellomrom; tom tekst eller ikke-tekst blir None."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def parse_date(value: object) -> str | None:
    """«18.09.2026» → «2026-09-18». Ukjente formater gir None; vi gjetter aldri på datoer."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def parse_timestamp(value: object) -> datetime | None:
    """ISO 8601-tidspunkt (f.eks. «2026-10-06T09:29:40.412Z») → norsk lokal tid, med sommer-/vintertid."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None  # uten tidssone vet vi ikke hvilket klokkeslett det egentlig er
    return moment.astimezone(OSLO)


def parse_jobbnorge(data: object) -> list[Job]:
    """Tolker svaret fra GET /v3/Jobs?employer=1562 i Jobbnorge Public API."""
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError("Jobbnorge: uventet JSON-format (mangler listen 'jobs')")
    jobs = []
    for item in data["jobs"]:
        number = item.get("id") if isinstance(item, dict) else None
        if not isinstance(number, int):
            log.warning("Jobbnorge: hopper over en annonse uten gyldig ID")
            continue
        places = []
        for location in item.get("locations") or []:
            place = clean(location.get("area") or location.get("municipality")) if isinstance(location, dict) else None
            if place and place not in places:
                places.append(place)
        department = ", ".join(part for part in (clean(item.get("department")), " / ".join(places)) if part)
        jobs.append(
            Job(
                id=f"jobbnorge:{number}",
                title=clean(item.get("title")) or NO_TITLE,
                url=JOBBNORGE_JOB_URL.format(number),
                source="jobbnorge",
                published_date=parse_date(item.get("publicationDate")),
                department=department or None,
                deadline=clean(item.get("deadline")),
            )
        )
    return jobs


def parse_student_feed(xml: bytes) -> list[Job]:
    """Tolker Atom-feeden for «Ledige studentstillinger på HiØ»."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SourceError(f"Studentstillinger: ugyldig XML ({exc})") from None
    if root.tag != f"{ATOM}feed":
        raise SourceError("Studentstillinger: svaret er ikke en Atom-feed")
    jobs = []
    for entry in root.findall(f"{ATOM}entry"):
        links = entry.findall(f"{ATOM}link")
        href = next((link.get("href") for link in links if link.get("rel", "alternate") == "alternate"), None)
        if not href:
            log.warning("Studentstillinger: hopper over en oppføring uten lenke")
            continue
        job_id, url = identify(urljoin(STUDENT_FEED_URL, href))
        published = parse_timestamp(entry.findtext(f"{ATOM}published"))
        jobs.append(
            Job(
                id=job_id,
                title=clean(entry.findtext(f"{ATOM}title")) or NO_TITLE,
                url=url,
                source="hiof-studentstillinger",
                published_date=published.date().isoformat() if published else None,
                published_time=published.strftime("%H:%M") if published else None,
            )
        )
    return jobs


def parse_hiof_page(html: str) -> list[Job]:
    """Tolker alle stillingslister (Vortex-feeds) på https://www.hiof.no/om/jobb/."""
    soup = BeautifulSoup(html, "html.parser")
    feeds = soup.select("div.vrtx-feed")
    if not feeds:
        raise SourceError("HiØ-siden: fant ingen stillingslister (div.vrtx-feed). HTML-strukturen kan ha endret seg.")
    jobs = []
    for link in (a for feed in feeds for a in feed.select("a.item-title[href]")):
        job_id, url = identify(urljoin(HIOF_PAGE_URL, link["href"]))
        jobs.append(Job(id=job_id, title=clean(link.get_text()) or NO_TITLE, url=url, source="hiof-jobbside"))
    return jobs


def fetch_jobbnorge(session) -> list[Job]:
    response = http_client.request(
        session,
        "GET",
        JOBBNORGE_API_URL,
        params={"employer": JOBBNORGE_EMPLOYER_ID},
        headers={"Accept": "application/json"},
    )
    try:
        data = response.json()
    except ValueError:
        raise SourceError("Jobbnorge: svaret er ikke gyldig JSON") from None
    return parse_jobbnorge(data)


def fetch_student_feed(session) -> list[Job]:
    return parse_student_feed(http_client.request(session, "GET", STUDENT_FEED_URL).content)


def fetch_hiof_page(session) -> list[Job]:
    return parse_hiof_page(http_client.request(session, "GET", HIOF_PAGE_URL).text)


# Rekkefølgen er prioritet: når samme annonse finnes i flere kilder, vinner den første,
# og senere kilder fyller bare inn felter som mangler.
SOURCES = (
    ("Jobbnorge-API", fetch_jobbnorge),
    ("HiØ studentstillinger (Atom-feed)", fetch_student_feed),
    ("HiØ jobbside (HTML)", fetch_hiof_page),
)
FILLABLE_FIELDS = ("published_date", "published_time", "department", "deadline")


def fetch_all_jobs(session) -> tuple[list[Job], list[str]]:
    """Henter alle kilder og slår sammen duplikater. Returnerer (stillinger, navn på kilder som feilet)."""
    merged: dict[str, Job] = {}
    failed = []
    for name, fetch in SOURCES:
        try:
            jobs = fetch(session)
        except (http_client.HttpError, SourceError) as exc:
            log.error("%s feilet: %s", name, exc)
            failed.append(name)
            continue
        log.info("%s: %d stillinger", name, len(jobs))
        for job in jobs:
            existing = merged.setdefault(job.id, job)
            for field in FILLABLE_FIELDS:
                if getattr(existing, field) is None:
                    setattr(existing, field, getattr(job, field))
    return list(merged.values()), failed
