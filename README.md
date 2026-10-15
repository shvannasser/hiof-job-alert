# hiof-job-alert – stillingsvarsler for Høgskolen i Østfold

Sjekker [hiof.no/om/jobb](https://www.hiof.no/om/jobb/) hver dag kl. 12:00 og 14:00 norsk tid med GitHub Actions og
sender nye stillinger til en Discord-kanal og til mobilen via ntfy. Ingen server, database eller betalte tjenester.

Slik ser et varsel ut (generert fra ekte data 12.10.2026):

```text
NY STILLING – Høgskolen i Østfold

Tittel: Ledig stilling som kommunikasjonssjef
Publisert: 18.09.2026 (klokkeslett ikke oppgitt)
Avdeling/sted: Organisasjonsavdelingen, Halden
Søknadsfrist: 18.10.2026
Lenke: https://www.jobbnorge.no/ledige-stillinger/stilling/307772
```

**Kom i gang:** gjør del F (oppsett), deretter G (første kjøring), og kryss av i H (sjekkliste).

---

## A. Teknisk vurdering av HiØ-siden og valgt hentemetode

Undersøkt 12.10.2026 med faktiske forespørsler:

| Spørsmål | Funn |
| --- | --- |
| Tilgang (403?) | `https://www.hiof.no/om/jobb/` svarte HTTP 200 fra en vanlig PC, både med appens egen User-Agent, `requests` sin standard og flere andre. 403 lot seg ikke gjenskape. `robots.txt` tillater `/om/jobb/`. Tilgang **fra GitHubs servere** kan først bekreftes ved første kjøring (del G). |
| Hvordan stillingene leveres | Vanlig server-rendret HTML fra publiseringssystemet Vortex, uten JavaScript-lasting. Siden har to lister (`div.vrtx-feed` → `a.item-title`): «Alle ledige stillinger hos HiØ i Jobbnorge» og «Ledige studentstillinger på HiØ». Listene viser bare tittel og lenke, ingen dato. |
| Offisielt API / feed | **Jobbnorge Public API v3** (dokumentert med Swagger på `publicapi.jobbnorge.no`): `GET /v3/Jobs?employer=1562` (1562 = HiØ) gir alle HiØ-stillinger som JSON med stabil `id`, tittel, avdeling, sted, `publicationDate` og søknadsfrist, uten paginering. **Vortex Atom-feed** for studentstillinger: `https://www.hiof.no/om/jobb/jobber-for-studenter/?vrtx=feed` med eksakt publiseringstidspunkt. |
| Publiseringstidspunkt | Jobbnorge oppgir bare dato (`dd.mm.åååå`, sjekket mot alle 1127 annonser i API-et). Studentstillingene har dato og klokkeslett (UTC i feeden, omregnet til norsk tid; stemmer med «Publisert» på selve siden). |
| Jobbnorge-annonsesidene | Rendres med JavaScript (AngularJS) og har ingen publiseringsdato i HTML. Brukes derfor ikke. |

**Valgt metode:** Appen henter tre kilder (tre forespørsler per kjøring) og slår dem sammen på stabil ID:

1. **Jobbnorge-API-et.** Strukturert, med dato, avdeling og sted.
2. **Atom-feeden for studentstillinger.** Strukturert, med dato og klokkeslett.
3. **Selve jobbsiden (HTML).** Dette er siden du ba om å overvåke. Den fanger opp alt siden viser, også om HiØ legger til nye lister eller bytter rekrutteringssystem.

Feiler én kilde, brukes de andre, og kjøringen markeres som feilet slik at du får beskjed.
Ingen tilgangskontroller omgås: appen identifiserer seg ærlig som `hiof-job-alert/1.0`.

---

## B. Kravspesifikasjon og begrensninger

| Krav | Løsning |
| --- | --- |
| Kjøre hver dag kl. 12:00 og 14:00 norsk tid | GitHub Actions `schedule` med `timezone: "Europe/Oslo"` (sommer-/vintertid håndteres av GitHub) |
| Manuell kjøring | `workflow_dispatch`, med valgfri testmelding |
| Varsle Discord og mobil | Discord Incoming Webhook og ntfy (JSON-publisering, støtter æøå) |
| Hente tittel, URL, ID, dato, klokkeslett, avdeling | Se tabellen i del A. Felter kilden ikke oppgir, lagres som `null` og vises som «ikke oppgitt» |
| Ikke finne på datoer | Kun datoer fra kilden. Oppdagelsestidspunktet lagres separat som `first_seen_at` |
| Stabil identifikasjon | `jobbnorge:<stillingsnummer>`; ellers normalisert URL (https, uten `#`, `index.html` og `/` til slutt) |
| Første kjøring varsler ikke | Eksisterende annonser registreres med `"baseline": true`. Krever at alle kilder svarer, ellers lagres ingenting |
| Ikke slette historikk ved feil | Historikken slettes aldri automatisk. Skadet fil overskrives aldri (kjøringen stopper) |
| Endrede annonser | Oppdateres i historikken uten nytt varsel |
| Duplikater | Én annonse per ID, også når den finnes i flere kilder |
| Timeout og begrensede retries | 20 s timeout, maks 3 forsøk med 5 s og 10 s ventetid, respekterer `Retry-After` (maks 60 s) |
| Feilhåndtering per kanal | Status `pending`/`sent`/`failed` spores separat for Discord og ntfy |
| Prøve på nytt | Mislykkede varsler prøves igjen ved hver kjøring, opptil 10 kjøringer |
| Logging uten hemmeligheter | Feil logges som «HTTP 500 etter 3 forsøk» eller «ConnectionError», aldri med URL. Testet automatisk |
| Vedvarende lagring | `data/seen_jobs.json` committes tilbake til (det private) repoet av arbeidsflyten |

### Begrensninger

- Jobbnorge oppgir ikke klokkeslett, så Jobbnorge-varsler viser «klokkeslett ikke oppgitt».
- En annonse som legges ut **og** fjernes mellom to kjøringer (innen to timer, eller over natten), oppdages ikke.
- GitHub kan forsinke planlagte kjøringer ved høy belastning. 12:00 og 14:00 er ikke minuttnøyaktig.
- Jobbnorge-API-et og HTML-strukturen kan endres uten varsel. Da feiler kjøringen med en tydelig melding, og de andre kildene brukes fortsatt.
- Studentstillinger har ingen stillings-ID og identifiseres med URL. Flytter HiØ en slik side, varsles den som ny.
- ntfy-emnet er bare beskyttet ved at navnet er hemmelig.

### Duplikatvern når noe feiler midt i en kjøring

1. Nye annonser lagres som `pending` i begge kanaler **før** noe sendes.
2. Etter **hvert** varsel lagres statusen straks (`sent`, eller `pending` med feilmelding). Lagringen er atomisk: filen skrives til en midlertidig fil og byttes inn, så den aldri blir halvskrevet.
3. Kanalene er uavhengige: feiler ntfy, sendes Discord-varselet likevel, og bare ntfy prøves på nytt neste gang.
4. Svarer en kanal ikke i det hele tatt (nettverksfeil, 5xx eller 429 etter 3 forsøk), settes resten av den kanalens varsler på vent til neste kjøring, så kjøringen ikke henger.
5. Lagringssteget i arbeidsflyten kjører også når skriptet feiler (`if: always()`), og `concurrency` hindrer to samtidige kjøringer.

Duplikater kan da bare oppstå hvis prosessen dør i millisekundene mellom «Discord/ntfy svarte OK» og «status lagret», hvis
et svar går tapt etter at meldingen faktisk ble levert, eller hvis `git push` av historikken feiler tre ganger på rad.

---

## C. Prosjektstruktur

```text
hiof-job-alert/
├── .github/workflows/
│   ├── daily-check.yml     Kjøring kl. 12:00 og 14:00 + manuell kjøring, lagrer historikken
│   └── tests.yml           Kjører testene ved hver push
├── src/
│   ├── main.py             Styrer kjøringen: hent → sammenlign → lagre → varsle → lagre
│   ├── scraper.py          Henter og tolker de tre kildene, stabil ID, sammenslåing
│   ├── notifier.py         Meldingsformat, Discord og ntfy
│   ├── storage.py          Historikk og varslingsstatus i JSON, atomisk lagring
│   └── http_client.py      Felles timeout, retries og feilmeldinger uten hemmeligheter
├── tests/
│   ├── fixtures/           Ekte svar fra HiØ, Atom-feeden og Jobbnorge (12.10.2026)
│   ├── helpers.py          Falsk HTTP-sesjon: testene går aldri mot nettet
│   ├── conftest.py
│   ├── test_scraper.py  test_storage.py  test_notifier.py  test_main.py
├── data/seen_jobs.json     Historikken (starter tom, fylles ved første kjøring)
├── requirements.txt        requests, beautifulsoup4, tzdata, pytest (låste versjoner)
├── pytest.ini
├── .env.example            Mal for lokal kjøring, uten ekte hemmeligheter
├── .gitignore              Holder .env, .venv og lokale filer utenfor git
└── README.md
```

Hver annonse i `data/seen_jobs.json` ser slik ut:

```json
"jobbnorge:307772": {
  "title": "Ledig stilling som kommunikasjonssjef",
  "url": "https://www.jobbnorge.no/ledige-stillinger/stilling/307772",
  "source": "jobbnorge",
  "published_date": "2026-09-18",
  "published_time": null,
  "department": "Organisasjonsavdelingen, Halden",
  "deadline": "18.10.2026",
  "first_seen_at": "2026-10-12T14:05:54+00:00",
  "baseline": false,
  "notifications": {
    "discord": {"status": "sent", "attempts": 1, "updated_at": "2026-10-12T14:05:54+00:00", "error": null},
    "ntfy": {"status": "sent", "attempts": 1, "updated_at": "2026-10-12T14:05:54+00:00", "error": null}
  }
}
```

---

## D. GitHub Actions

**`.github/workflows/daily-check.yml`**

- **Tidsplan:** `cron: "0 12,14 * * *"` med `timezone: "Europe/Oslo"`. GitHub støtter tidssone i `on.schedule` (bekreftet i GitHubs
  dokumentasjon), og justerer selv for sommer- og vintertid. Uten tidssonestøtte måtte man brukt
  forskjellige UTC-tider om sommeren og vinteren.
- **Forsinkelser:** GitHub kan forsinke planlagte kjøringer. En forsinket eller utelatt kjøring tas igjen ved neste kjøring, fordi
  appen sammenligner mot historikken og ikke mot tidspunkt.
- **Manuell kjøring:** `workflow_dispatch` med avkrysningsboksen «Send bare en testmelding».
- **Rettigheter:** bare `contents: write`, som trengs for å pushe historikken. Tokenet gjelder bare dette repoet og utløper når jobben er ferdig.
- **Samtidighet:** `concurrency` sørger for at bare én kjøring skriver historikken om gangen.
- **Lagring:** committer `data/seen_jobs.json` bare når den er endret (gjør `git pull --rebase` og prøver opptil tre ganger).
  Det blir altså ikke en commit hver dag, bare når noe har skjedd.
- **Planlagte kjøringer bruker koden på standardgrenen** (`main`).

**`.github/workflows/tests.yml`** kjører alle testene ved hver push og pull request, med bare lesetilgang.
Commits som arbeidsflyten selv lager med `GITHUB_TOKEN`, starter ikke nye kjøringer.

---

## E. Automatiske tester

Testene bruker ekte svar lagret i `tests/fixtures/` og en falsk HTTP-sesjon. De kontakter aldri HiØ, Jobbnorge, Discord eller ntfy.

**Windows (PowerShell):**

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -v
```

**macOS/Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -v
```

| Krav | Tester |
| --- | --- |
| Parsing av annonser | `test_scraper.py`: `test_parse_hiof_page_*`, `test_parse_jobbnorge_*`, `test_parse_student_feed_*` |
| Stabil identifikasjon | `test_jobbnorge_ads_are_identified_by_job_number`, `test_other_ads_are_identified_by_normalized_url`, `test_same_job_from_several_sources_is_merged` |
| Første kjøring uten varsler | `test_first_run_registers_existing_jobs_without_notifying`, `test_first_run_saves_nothing_if_a_source_fails` |
| Ny annonse | `test_new_job_is_notified_once_in_each_channel`, `test_job_only_on_hiof_page_is_notified_with_missing_date` |
| Duplikatbeskyttelse | `test_no_duplicate_notifications_on_later_runs`, `test_duplicate_within_one_source_gives_one_job`, `test_empty_sources_never_delete_history_and_returning_jobs_are_not_renotified`, `test_changed_job_is_updated_but_not_notified_again` |
| Manglende publiseringstidspunkt | `test_publication_time_is_shown_only_when_the_source_gives_it`, `test_parse_jobbnorge_never_invents_missing_metadata`, `test_parse_timestamp_handles_winter_time_and_never_guesses` |
| Feil fra nettsiden | `test_website_errors_leave_history_untouched`, `test_403_from_website_is_reported_without_hammering`, `test_other_sources_are_used_when_one_fails`, retry-testene i `test_scraper.py` |
| Feil fra Discord | `test_discord_failure_does_not_affect_ntfy_and_is_retried_next_run`, `test_unavailable_channel_is_paused_for_the_rest_of_the_run`, `test_rejected_message_does_not_block_the_next_one` |
| Feil fra ntfy | `test_ntfy_failure_does_not_affect_discord_and_is_retried_next_run` |
| Vedvarende lagring | `test_storage.py`: roundtrip, atomisk skriving, skadet fil overskrives aldri, den medfølgende `data/seen_jobs.json` er gyldig |
| Trygg oppdatering av varslingsstatus | `test_crash_mid_run_keeps_sent_status_and_avoids_duplicates`, `test_notification_status_is_tracked_separately_per_channel`, `test_gives_up_after_max_attempts`, `test_secrets_never_reach_logs_or_history` |

---

## F. Oppsett steg for steg

### F1. Discord-webhook

1. Åpne Discord og gå til en server der du har rettigheten «Administrer webhooks» (din egen server holder).
2. **Serverinnstillinger → Integrasjoner → Webhooks → Ny webhook.**
3. Gi den et navn, for eksempel `HiØ-stillinger`, og velg kanalen varslene skal til.
4. Trykk **Kopier webhook-URL**. URL-en ser ut som `https://discord.com/api/webhooks/<tall>/<token>`.
   Behandle den som et passord: alle som har den, kan poste i kanalen.

### F2. ntfy på mobilen

1. Installer appen **ntfy** (Google Play / F-Droid for Android, App Store for iPhone).
2. Lag et langt, tilfeldig emnenavn. Navnet fungerer som et passord. I PowerShell:

   ```powershell
   "hiof-jobb-" + [guid]::NewGuid().ToString("N")
   ```

   Tillatte tegn er A–Z, a–z, 0–9, `-` og `_`, maks 64 tegn.
3. I appen: trykk **+** (abonner på emne), lim inn emnenavnet og behold serveren `ntfy.sh`. Trykk **Abonner**.

### F3. GitHub-repository

1. Logg inn på github.com → **+** øverst til høyre → **New repository**.
2. Navn: `hiof-job-alert`. Velg **Private**. Ikke kryss av for README, .gitignore eller lisens. Trykk **Create repository**.
3. Last opp prosjektet fra denne mappen (PowerShell). Git-repoet er allerede initialisert lokalt med grenen `main`,
   men ingenting er committet:

   ```powershell
   cd "$HOME\Desktop\hiofJobbsøk"
   git config --global user.name "Ditt Navn"            # bare hvis Git ikke er satt opp før
   git config --global user.email "din@epost.no"         # bare hvis Git ikke er satt opp før
   git add .
   git status                                            # .env og .venv skal IKKE stå i listen
   git commit -m "Stillingsvarsler for HiØ"
   git remote add origin https://github.com/<ditt-brukernavn>/hiof-job-alert.git
   git push -u origin main
   ```

   Første `git push` åpner et innloggingsvindu for GitHub.

### F4. Secrets

I repoet på GitHub: **Settings → Secrets and variables → Actions → New repository secret.** Legg inn én eller begge, avhengig av hvor du vil ha varsler:

| Name | Secret |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | Webhook-URL-en fra F1 |
| `NTFY_TOPIC` | Emnenavnet fra F2 (bare navnet, ikke `https://ntfy.sh/...`) |

Mangler en av dem, brukes bare den andre kanalen. Ingen andre secrets trengs. Tokenet for å lagre historikken (`GITHUB_TOKEN`) lager GitHub automatisk.

---

## G. Første kjøring, manuell testing og logger

### G1. Testmelding (verifiserer secrets, Discord og ntfy)

1. Gå til fanen **Actions** i repoet. Velg **Daglig stillingssjekk** til venstre.
2. Trykk **Run workflow**, kryss av for **«Send bare en testmelding…»** og trykk den grønne **Run workflow**.
3. Etter under ett minutt skal det komme en melding som starter med «TEST – dette er en testmelding» i Discord og på mobilen.
   Historikken endres ikke.

### G2. Første ordinære kjøring

1. **Run workflow** igjen, denne gangen uten avkrysning.
2. Åpne kjøringen → jobben **check** → steget **Se etter nye stillinger**. Forventet logg:

   ```text
   INFO Jobbnorge-API: 1 stillinger
   INFO HiØ studentstillinger (Atom-feed): 2 stillinger
   INFO HiØ jobbside (HTML): 3 stillinger
   INFO Første kjøring: 3 eksisterende stillinger er registrert uten varsel. Nye stillinger varsles fra neste kjøring.
   ```

   Tallene er dagens; de endrer seg når HiØ legger ut eller fjerner stillinger. Står det `HTTP 403`, blokkerer HiØ GitHubs
   servere (se feilsøking under).
3. Det skal komme en ny commit, **«Oppdater stillingshistorikk»**, fra `github-actions[bot]`. Da er `data/seen_jobs.json` fylt ut.

### G3. Ende-til-ende-test av et ekte varsel

1. Åpne `data/seen_jobs.json` på GitHub og trykk blyanten (Edit).
2. Velg en stilling som fortsatt står på hiof.no/om/jobb, og legg til `-test` i nøkkelen dens, for eksempel
   `"jobbnorge:307772"` → `"jobbnorge:307772-test"`. Endre bare nøkkelen, ikke noe annet. Trykk **Commit changes**.
3. **Run workflow** uten avkrysning. Stillingen regnes nå som ny og skal varsles i både Discord og ntfy.
   I historikken får den `"status": "sent"` for begge kanaler.
4. Kjør én gang til: nå skal det **ikke** komme noe varsel (duplikatvern). Oppføringen med `-test` kan du slette eller la ligge.

### G4. Logger og feilsøking

- **Logger:** Actions → velg kjøring → **check** → klikk på et steg. Viktige linjer: `Ny stilling: …`,
  `discord: varslet om …`, `ntfy: varslet om …` og oppsummeringen `Ferdig: … Alle varsler levert: ja`.
- **Avslutningskoder:** 0 = OK. 1 = en kilde eller et varsel feilet (det som lyktes, er lagret, og resten prøves neste gang).
  2 = feil i oppsettet, for eksempel manglende secret eller skadet historikkfil (ingenting er gjort).
- **E-post ved feil:** GitHub sender som standard e-post når en planlagt kjøring feiler, til den som sist endret
  cron-linjen. Sjekk at **Settings → Notifications → Actions** hos deg varsler om feilede kjøringer.

| Melding i loggen | Hva du gjør |
| --- | --- |
| `Mangler DISCORD_WEBHOOK_URL og NTFY_TOPIC` | Legg inn minst én secret (F4). Navnene må være nøyaktig like |
| `discord: … (HTTP 401)` eller `(HTTP 404)` | Webhooken er slettet eller URL-en er feil. Lag ny (F1) og oppdater secret |
| `HiØ jobbside (HTML) feilet: HTTP 403` | HiØ avviser forespørsler fra GitHubs servere. Appen omgår ikke dette. Kontakt HiØ (`nettredaksjon@hiof.no`), eller kjør skriptet fra egen PC (G5) med Oppgaveplanlegging i Windows |
| `fant ingen stillingslister (div.vrtx-feed)` | HiØ har endret siden. De andre kildene brukes fortsatt, men parseren i `src/scraper.py` må oppdateres |
| `Klarte ikke å lagre historikken i repoet` | Sjekk at `main` ikke har grenbeskyttelse som stopper `github-actions[bot]` |
| `Historikken i … har et ukjent format` | `data/seen_jobs.json` er ugyldig JSON (ofte etter manuell redigering). Rett feilen eller tilbakestill filen i historikken på GitHub |

### G5. Kjøre lokalt

```powershell
.\.venv\Scripts\python.exe src\main.py --dry-run             # henter og viser, sender og lagrer ingenting
copy .env.example .env                                        # fyll inn ekte verdier i .env
.\.venv\Scripts\python.exe src\main.py --test-notification   # testmelding til begge kanaler
.\.venv\Scripts\python.exe src\main.py                       # vanlig kjøring (endrer data/seen_jobs.json lokalt)
```

Bruker du både lokal kjøring og GitHub Actions, bør du ikke committe en lokalt endret `data/seen_jobs.json`.
Kjør `git checkout -- data/seen_jobs.json` før du pusher kodeendringer.

---

## H. Sjekkliste ende til ende

- [ ] `pytest` er grønt lokalt (71 tester), og **Tester** er grønn under Actions etter første push.
- [ ] Repoet er **Private**, og `.env` finnes ikke på GitHub.
- [ ] Minst én secret finnes: `DISCORD_WEBHOOK_URL` og/eller `NTFY_TOPIC`.
- [ ] G1: testmeldingen kom fram i Discord **og** på mobilen.
- [ ] G2: første kjøring er grønn, loggen viser alle tre kildene uten `403`, og `github-actions[bot]` har committet historikken.
- [ ] G3: den «nye» stillingen ble varslet i begge kanaler, og en ny kjøring varslet ikke på nytt.
- [ ] Dagen etter: under Actions finnes en kjøring med hendelsen **schedule**, rundt kl. 12:00 og 14:00 eller litt senere.
- [ ] Du får e-post fra GitHub når en kjøring feiler (G4).

---

## I. Gratis kvoter, begrensninger og driftskostnader

| Tjeneste | Gratis kvote / grense | Forventet bruk |
| --- | --- | --- |
| GitHub Actions, privat repo (GitHub Free) | 2000 minutter per måned, 500 MB artefaktlagring | ca. 1 minutt per kjøring, rundt 60–90 minutter i måneden inkludert testkjøringer |
| GitHub Actions, offentlig repo | Gratis med standard runnere | Frarådes: historikken blir offentlig, og planlagte kjøringer slås av etter 60 dager uten aktivitet i repoet |
| Når kvoten er brukt opp | Uten betalingsmåte blokkeres videre bruk; du blir ikke belastet | Ikke aktuelt med dette forbruket |
| ntfy.sh (uten konto) | 250 meldinger per døgn, 4096 byte per melding | Noen få meldinger i uken |
| Discord-webhook | Gratis, med fartsgrense per webhook | Appen venter automatisk ved HTTP 429 |
| HiØ og Jobbnorge | Offentlige sider og API | Seks GET-forespørsler per døgn |
| Lagring i repoet | Historikken vokser noen hundre byte per stilling | Godt under 1 MB per år |

**Forventet driftskostnad: 0 kr.**

## Vedlikehold

- Ingen fast vedlikehold. Går noe galt, feiler kjøringen, og du får e-post.
- Arbeidsflyten committer historikken til GitHub. Kjør derfor `git pull --rebase` før du pusher egne kodeendringer.
- Oppdater gjerne versjonene i `requirements.txt` og `actions/*@v7` en gang i året, og kjør testene etterpå.
- `MAX_ATTEMPTS` i `src/storage.py` styrer hvor mange kjøringer et mislykket varsel prøves (standard 10). Gitt opp-varsler
  får status `failed`. Sett status tilbake til `pending` i `data/seen_jobs.json` for å prøve igjen.
