"""Waarneembaarheid: één plek waar de ronde vertelt wat ze doet, en één plek
die verhindert dat ze daarbij een geheim uitspreekt.

Aanleiding (2026-08-29, ronde 2 tegen de echte bron): de ronde draaide negen
minuten zonder één regel output en eindigde op `400 Client Error: Bad Request`.
De reden stond in de PostgREST-responsebody, die `raise_for_status()` weggooit.
Drie defecten op rij op dezelfde naad waren daardoor alleen te vinden door de
hele ronde opnieuw te draaien — telkens ten koste van een loginpoging.

Twee regels sturen alles hier:

1. **Een fout draagt zijn eigen reden.** Een niet-2xx antwoord wordt nooit
   kaal doorgegeven; status, content-type en een afgeknotte body gaan mee in
   de melding. Zonder de body is een 400 van PostgREST niet te diagnosticeren.
2. **Een geheim verlaat dit proces niet.** Namen van variabelen, cookies,
   tabellen en kolommen mogen in een logregel; waarden van wachtwoorden,
   sleutels en cookies nooit. De discipline zit in de aanroepers, het vangnet
   in `SecretRedactingFilter`: die schrapt elke geregistreerde geheime waarde
   uit elke regel, ook uit een body die per ongeluk te veel draagt.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Iterable, Optional, TextIO

LOGGER_NAME = "simpul_extract"
DEFAULT_LEVEL = "INFO"
REDACTED = "***"

# Een body wordt afgeknot: genoeg om een PostgREST-fout (`message`, `details`,
# `hint`, `code`) volledig te tonen, te weinig om een hele HTML-pagina of een
# datadump in de logs te laten belanden.
MAX_BODY_CHARS = 2000

# Onder deze lengte wordt een "geheim" niet geschrapt: een wachtwoord van twee
# tekens zou anders elke `e` uit elke logregel wegvegen. Echte sleutels en
# wachtwoorden zijn ruim langer.
MIN_SECRET_LENGTH = 6


class SecretRedactingFilter(logging.Filter):
    """Schrapt geregistreerde geheime waarden uit elke logregel.

    Dit is een vangnet, geen vergunning: een aanroeper hoort een wachtwoord,
    sleutel of cookiewaarde sowieso niet aan de logger aan te bieden. Het
    vangnet bestaat omdat de gevaarlijkste regel de regel is die niemand
    bewust schreef — een responsebody die een token echoot, bijvoorbeeld.

    De set is muteerbaar omdat niet elk geheim uit de omgeving komt: de
    cookiewaarden die de sessie onderweg wint, komen er tijdens de ronde bij.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: set = set()
        self.add(secrets)

    def add(self, secrets: Iterable[str]) -> None:
        for secret in secrets:
            if secret and len(secret) >= MIN_SECRET_LENGTH:
                self._secrets.add(secret)

    def scrub(self, text: str) -> str:
        # Langste eerst: een sleutel die een kortere waarde bevat moet in zijn
        # geheel verdwijnen, niet half.
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        # `getMessage()` past de `%`-argumenten al toe; daarna zijn ze verwerkt
        # en mogen ze weg, anders formatteert de handler nog een keer.
        record.msg = self.scrub(record.getMessage())
        record.args = ()
        if record.exc_text:
            record.exc_text = self.scrub(record.exc_text)
        return True


_redactor = SecretRedactingFilter()

# Zonder handler valt `logging` terug op `lastResort`, dat elke WARNING en
# ERROR kaal naar stderr schrijft -- ook in tests die helemaal geen logging
# configureren, en dus buiten de redactiefilter om. Een NullHandler bij import
# maakt zwijgen de standaard: alleen `configure_logging()` opent de kraan.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


def add_secrets(*values: Optional[str]) -> None:
    """Registreert waarden die nooit in een logregel mogen verschijnen."""
    _redactor.add([value for value in values if value])


def secrets_from_env(env) -> tuple:
    """De geheimen die de PRD-`docker run` meegeeft. `SIMPUL_USERNAME` hoort
    er bewust bij: het is geen sleutel, maar wel een credential, en een
    logregel heeft hem nergens voor nodig."""
    return (
        env.get("SIMPUL_PASSWORD"),
        env.get("SUPABASE_SECRET_KEY"),
        env.get("SIMPUL_USERNAME"),
    )


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(env=None, stream: Optional[TextIO] = None) -> logging.Logger:
    """Zet één handler op stderr, met tijdstempel en niveau, en hangt de
    redactiefilter zowel op de logger als op de handler.

    Op de handler omdat een `logging.Filter` op een logger niet meegaat met
    regels die via een child-logger binnenkomen; op de logger omdat een
    aanroeper de handler kan vervangen. Twee keer schrappen is niet erger dan
    één keer, en één keer vergeten is het wel.

    Naar **stderr**, niet stdout: stdout draagt het slotoverzicht dat de PO
    leest, en dat moet leesbaar blijven zonder de voortgangsregels ertussen.
    """
    env = env if env is not None else {}
    add_secrets(*secrets_from_env(env))

    level_name = (env.get("LOG_LEVEL") or DEFAULT_LEVEL).upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(_redactor)
    logger.addFilter(_redactor)
    logger.addHandler(handler)
    return logger


def body_snippet(response: Any, max_chars: int = MAX_BODY_CHARS) -> str:
    """De responsebody als tekst, afgeknot en ontdaan van geregistreerde
    geheimen. Faalt nooit: een body die niet te lezen is mag geen tweede fout
    veroorzaken bovenop de fout die we juist proberen te verklaren."""
    if response is None:
        return ""
    try:
        text = getattr(response, "text", "") or ""
    except Exception:  # pragma: no cover - defensief
        return "<body niet leesbaar>"
    if not isinstance(text, str):
        text = str(text)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[:max_chars] + f"… (+{len(text) - max_chars} tekens)"
    return _redactor.scrub(text)


def describe_http_response(response: Any) -> str:
    """Beschrijft een respons in termen die een faalmelding mag dragen:
    status, content-type en de body.

    Anders dan `session.describe_response`, dat bewust géén body toont omdat
    het daar om een sessieprobe gaat waarvan de body een hele pagina kan zijn,
    hoort de body hier juist wél in de melding: een 400 van PostgREST bestaat
    volledig uit die body. De redactiefilter houdt geheimen eruit.
    """
    if response is None:
        return "geen respons"
    parts = [f"status {getattr(response, 'status_code', '?')}"]
    headers = getattr(response, "headers", None) or {}
    content_type = headers.get("Content-Type", "")
    if content_type:
        parts.append(f"content-type {content_type}")
    body = body_snippet(response)
    parts.append(f"body {body}" if body else "lege body")
    return ", ".join(parts)
