"""Toetst de twee beloftes van `simpul_extract.observability`.

1. Een fout draagt zijn eigen reden: `describe_http_response` zet de
   responsebody in de melding. Dat is precies wat `raise_for_status()` niet
   deed en waardoor drie defecten op de `extraction_run`-naad alleen te
   vinden waren door een hele ronde opnieuw te draaien.
2. Een geheim verlaat dit proces niet: elke geregistreerde waarde wordt uit
   elke logregel en elke body geschrapt.

De tweede belofte is de gevaarlijkste, want logging voegt juist output toe op
plekken waar eerst niets stond. De tegen-pin hieronder toont dat de filter
werkelijk bijt: dezelfde body zónder registratie draagt het geheim wél.

Netwerkloos: er verlaat geen verzoek dit proces.
"""
from __future__ import annotations

import logging
import io
import unittest

from simpul_extract.observability import (
    MIN_SECRET_LENGTH,
    REDACTED,
    SecretRedactingFilter,
    add_secrets,
    body_snippet,
    configure_logging,
    describe_http_response,
    secrets_from_env,
)


class _Response:
    def __init__(self, status_code=400, body="", headers=None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {}


# Een echte PostgREST-fout, met de hand overgetypt uit de documentatie: dit is
# de vorm waarin de reden van een 400 aankomt. Geen daadwerkelijk antwoord van
# het project, dus geen geheim.
POSTGREST_FOUT = (
    '{"code":"PGRST204",'
    '"details":null,'
    '"hint":null,'
    '"message":"Could not find the \'fetched_at\' column of '
    '\'extraction_run\' in the schema cache"}'
)


class TestFoutDraagtZijnRedenMee(unittest.TestCase):
    def test_body_staat_in_de_beschrijving(self):
        beschrijving = describe_http_response(
            _Response(400, POSTGREST_FOUT, {"Content-Type": "application/json"})
        )

        self.assertIn("status 400", beschrijving)
        self.assertIn("PGRST204", beschrijving)
        self.assertIn("schema cache", beschrijving)

    def test_lege_body_wordt_als_zodanig_gemeld(self):
        self.assertIn("lege body", describe_http_response(_Response(500, "")))

    def test_lange_body_wordt_afgeknot_maar_niet_verzwegen(self):
        lang = "x" * 5000
        snippet = body_snippet(_Response(400, lang), max_chars=100)

        self.assertTrue(snippet.startswith("x" * 100))
        self.assertIn("+4900 tekens", snippet)

    def test_onleesbare_body_veroorzaakt_geen_tweede_fout(self):
        class Kapot:
            status_code = 400
            headers = {}

            @property
            def text(self):
                raise RuntimeError("body niet te decoderen")

        # De melding die we juist proberen te schrijven mag niet zelf omvallen.
        self.assertIn("niet leesbaar", body_snippet(Kapot()))


class TestGeheimVerlaatHetProcesNiet(unittest.TestCase):
    def test_geregistreerde_waarde_verdwijnt_uit_een_logregel(self):
        wachtwoord = "een-heel-geheim-wachtwoord"
        stroom = io.StringIO()
        logger = configure_logging(
            {"SIMPUL_PASSWORD": wachtwoord, "LOG_LEVEL": "INFO"}, stream=stroom
        )

        logger.info("login gestuurd met password=%s", wachtwoord)

        uitvoer = stroom.getvalue()
        self.assertNotIn(wachtwoord, uitvoer)
        self.assertIn(REDACTED, uitvoer)
        # De naam mag blijven staan: die verklaart de fout, de waarde niet.
        self.assertIn("password=", uitvoer)

    def test_tegenpin_zonder_registratie_lekt_dezelfde_waarde(self):
        """Tegen-pin: de filter moet werkelijk bijten.

        Dezelfde waarde die hierboven verdwijnt, blijft hier staan omdat ze
        nooit geregistreerd is. Zonder deze toets zou een filter die niets
        doet er net zo geslaagd uitzien als een filter die werkt.
        """
        niet_geregistreerd = "deze-waarde-is-nooit-aangemeld"
        filter_ = SecretRedactingFilter(["iets-anders-geheims"])

        self.assertIn(niet_geregistreerd, filter_.scrub(f"body {niet_geregistreerd}"))
        self.assertEqual(filter_.scrub("body iets-anders-geheims"), f"body {REDACTED}")

    def test_geheim_in_een_responsebody_wordt_ook_geschrapt(self):
        sleutel = "sb-secret-0123456789abcdef"
        add_secrets(sleutel)

        beschrijving = describe_http_response(
            _Response(401, '{"message":"invalid key ' + sleutel + '"}')
        )

        self.assertNotIn(sleutel, beschrijving)
        self.assertIn(REDACTED, beschrijving)

    def test_te_korte_waarde_wordt_niet_geschrapt(self):
        """Een geheim van twee tekens zou elke logregel onleesbaar maken door
        overal losse letters weg te vegen. Onder de drempel schrapt de filter
        daarom niets — dat is een bewuste grens, geen omissie."""
        kort = "a" * (MIN_SECRET_LENGTH - 1)
        filter_ = SecretRedactingFilter([kort])

        self.assertEqual(filter_.scrub(f"tekst met {kort} erin"), f"tekst met {kort} erin")

    def test_alle_drie_de_credentials_uit_de_omgeving_worden_geregistreerd(self):
        env = {
            "SIMPUL_PASSWORD": "wachtwoord-hier",
            "SUPABASE_SECRET_KEY": "sleutel-hier",
            "SIMPUL_USERNAME": "gebruiker-hier",
        }

        geheimen = secrets_from_env(env)

        self.assertEqual(set(geheimen), set(env.values()))


class TestLoggingGaatNaarStderrEnNietNaarStdout(unittest.TestCase):
    def test_handler_schrijft_naar_de_meegegeven_stroom(self):
        stroom = io.StringIO()
        logger = configure_logging({"LOG_LEVEL": "INFO"}, stream=stroom)

        logger.info("een regel")

        self.assertIn("een regel", stroom.getvalue())

    def test_log_level_uit_de_omgeving_wordt_gevolgd(self):
        stroom = io.StringIO()
        logger = configure_logging({"LOG_LEVEL": "WARNING"}, stream=stroom)

        logger.info("deze niet")
        logger.warning("deze wel")

        self.assertNotIn("deze niet", stroom.getvalue())
        self.assertIn("deze wel", stroom.getvalue())

    def test_een_child_logger_gaat_door_dezelfde_filter(self):
        """De modules loggen onder `simpul_extract.<module>`. Een filter die
        alleen op de hoofdlogger hangt, ziet die regels niet — daarom hangt
        hij ook op de handler."""
        sleutel = "child-logger-geheim-0123456789"
        stroom = io.StringIO()
        configure_logging({"SUPABASE_SECRET_KEY": sleutel}, stream=stroom)

        logging.getLogger("simpul_extract.storage").info("sleutel %s", sleutel)

        self.assertNotIn(sleutel, stroom.getvalue())
        self.assertIn(REDACTED, stroom.getvalue())


if __name__ == "__main__":
    unittest.main()
