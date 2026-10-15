"""Varslingsformat og nøyaktig hva som sendes til Discord og ntfy."""

import pytest

import notifier
from helpers import DISCORD_URL, NTFY_TOPIC, FakeSession

RECORD = {
    "title": "Ledig stilling som kommunikasjonssjef",
    "url": "https://www.jobbnorge.no/ledige-stillinger/stilling/307772",
    "published_date": "2026-09-18",
    "published_time": None,
    "department": "Organisasjonsavdelingen, Halden",
    "deadline": "18.10.2026",
}


def test_publication_time_is_shown_only_when_the_source_gives_it():
    assert notifier.published_text({"published_date": "2026-10-06", "published_time": "11:29"}) == "06.10.2026 kl. 11:29"
    assert notifier.published_text({"published_date": "2026-09-18", "published_time": None}) == "18.09.2026 (klokkeslett ikke oppgitt)"
    assert notifier.published_text({"published_date": None, "published_time": None}) == "ikke oppgitt av kilden"


def test_message_follows_the_agreed_format():
    assert notifier.message_lines(RECORD) == [
        "Tittel: Ledig stilling som kommunikasjonssjef",
        "Publisert: 18.09.2026 (klokkeslett ikke oppgitt)",
        "Avdeling/sted: Organisasjonsavdelingen, Halden",
        "Søknadsfrist: 18.10.2026",
        "Lenke: https://www.jobbnorge.no/ledige-stillinger/stilling/307772",
    ]


def test_optional_lines_are_left_out_when_missing():
    lines = notifier.message_lines({**RECORD, "department": None, "deadline": None})

    assert [line.split(":")[0] for line in lines] == ["Tittel", "Publisert", "Lenke"]


def test_long_titles_are_shortened_so_the_link_survives():
    lines = notifier.message_lines({**RECORD, "title": "x" * 5000})

    assert len(lines[0]) <= len("Tittel: ") + notifier.MAX_TITLE_LENGTH
    assert lines[-1] == f"Lenke: {RECORD['url']}"


def test_discord_message():
    session = FakeSession()
    session.set(DISCORD_URL, 200)

    notifier.DiscordNotifier(DISCORD_URL, session).send({**RECORD, "title": "Stipendiat i *KI* @everyone"})

    [call] = session.calls
    assert call["method"] == "POST"
    assert call["params"] == {"wait": "true"}
    content = call["json"]["content"]
    assert content.startswith("**NY STILLING – Høgskolen i Østfold**\n\n")
    assert "Tittel: Stipendiat i \\*KI\\* @everyone" in content
    assert content.endswith("Lenke: https://www.jobbnorge.no/ledige-stillinger/stilling/307772")  # klikkbar i Discord
    assert call["json"]["allowed_mentions"] == {"parse": []}


def test_ntfy_message():
    session = FakeSession()
    session.set(notifier.NTFY_SERVER, 200)

    notifier.NtfyNotifier(NTFY_TOPIC, session).send(RECORD)

    [call] = session.calls
    payload = call["json"]
    assert call["method"] == "POST"
    assert payload["topic"] == NTFY_TOPIC
    assert payload["title"] == "NY STILLING – Høgskolen i Østfold"
    assert payload["message"] == "\n".join(notifier.message_lines(RECORD))
    assert payload["click"] == RECORD["url"]  # trykk på varselet åpner annonsen
    assert payload["actions"] == [{"action": "view", "label": "Åpne annonsen", "url": RECORD["url"]}]


def test_ntfy_accepts_full_topic_url_for_own_server():
    session = FakeSession()
    session.set("https://ntfy.example.com", 200)

    notifier.NtfyNotifier("https://ntfy.example.com/mitt_emne", session).send(RECORD)

    assert session.calls[0]["json"]["topic"] == "mitt_emne"


@pytest.mark.parametrize(
    ("notifier_class", "value"),
    [
        (notifier.DiscordNotifier, "http://discord.com/api/webhooks/1/hemmelig"),
        (notifier.DiscordNotifier, "https://example.com/hemmelig"),
        (notifier.NtfyNotifier, "emne med mellomrom"),
        (notifier.NtfyNotifier, "x" * 65),
    ],
)
def test_invalid_config_is_rejected_without_revealing_the_value(notifier_class, value):
    with pytest.raises(notifier.ConfigError) as error:
        notifier_class(value, FakeSession())

    assert value not in str(error.value)
