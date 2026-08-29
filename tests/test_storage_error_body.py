"""Toetst dat een geweigerd schrijfverzoek zijn reden meedraagt.

Aanleiding: ronde 2 tegen de echte bron (2026-08-29) viel na negen minuten om
op `400 Client Error: Bad Request for url: .../extraction_run`. Die melding
zegt niet wélke kolom of welk type niet klopt — dat staat in de responsebody,
die `raise_for_status()` weggooit. Zonder body was het defect alleen te vinden
door de hele ronde opnieuw te draaien, telkens ten koste van een loginpoging
(`adr/2026-08-29-job-logt-zelf-in.md`).

De toets zit bewust op `PostgrestUpsertStore` zelf en niet op een losse
hulpfunctie: het vorige defect ontsnapte precies doordat de toets een laag
dieper aangreep dan de fout. Een naadtoets die `append()` overslaat, toetst de
naad niet.

Netwerkloos: elke sessie is een stub, er verlaat geen verzoek dit proces.
"""
from __future__ import annotations

import unittest

from simpul_extract.observability import REDACTED, add_secrets
from simpul_extract.storage import (
    PostgrestUpsertStore,
    StorageError,
    StorageWriteError,
)

API_KEY = "sb-secret-sleutel-die-nooit-in-een-melding-mag"

# Met de hand overgetypte PostgREST-fouten, in de vorm waarin ze binnenkomen.
KOLOM_ONBEKEND = (
    '{"code":"PGRST204","details":null,"hint":null,'
    '"message":"Could not find the \'fetched_at\' column of '
    '\'extraction_run\' in the schema cache"}'
)
IDENTITY_GEWEIGERD = (
    '{"code":"428C9","details":null,'
    '"hint":"Use OVERRIDING SYSTEM VALUE to override.",'
    '"message":"cannot insert a non-DEFAULT value into column \\"id\\""}'
)


class _Response:
    """Bootst een `requests.Response` na, mét `raise_for_status()`.

    Die methode hoort hier expliciet bij. Zonder haar zou de tegen-pin hieronder
    rood worden op een `AttributeError` in plaats van op de bewering die hij
    moet bewaken — dezelfde vorm van bijna-toets die eerder een heel defect
    liet passeren
    (`Context/lessons/2026-08-29-een-gestubde-naad-is-geen-getoetste-naad.md`).
    """

    def __init__(self, status_code, body="", headers=None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {"Content-Type": "application/json"}

    def raise_for_status(self):
        """Precies zoals requests het doet: status en url, geen body."""
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error: Bad Request for url: "
                f"https://example.invalid/rest/v1/extraction_run"
            )


def _melding_bij_schrijffout(store, table, rows):
    """Voert de schrijfactie uit en geeft de tekst van de fout terug, welke
    fout het ook is.

    Bewust niet `assertRaises(StorageWriteError)`: dan zou de tegen-pin rood
    worden omdát het type veranderde, niet omdat de melding zijn reden mist.
    De bewering is "de melding draagt de body" en daarop moet hij breken.
    """
    try:
        store.append(table, rows) if table == "extraction_run" else store.upsert(table, rows)
    except Exception as exc:  # noqa: BLE001 - het type is hier juist niet de bewering
        return str(exc)
    raise AssertionError("schrijfactie faalde niet, terwijl PostgREST 400 gaf")


class _StubSession:
    """Geeft één vooraf ingesteld antwoord terug op elke POST."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, json=None, params=None, headers=None):
        self.calls.append({"url": url, "json": json, "params": params, "headers": headers})
        return self._response


def _store(response):
    return PostgrestUpsertStore(
        base_url="https://example.invalid/rest/v1",
        api_key=API_KEY,
        session=_StubSession(response),
    )


class TestAppendMeldtWaaromPostgrestWeigerde(unittest.TestCase):
    def test_de_melding_draagt_de_postgrest_body(self):
        store = _store(_Response(400, KOLOM_ONBEKEND))

        melding = _melding_bij_schrijffout(
            store, "extraction_run", [{"entity": "customer", "rows_stored": 951}]
        )

        self.assertIn("PGRST204", melding)
        self.assertIn("fetched_at", melding)
        self.assertIn("schema cache", melding)

    def test_tegenpin_de_kale_status_alleen_is_niet_genoeg(self):
        """Tegen-pin: de oude melding bestond volledig uit statustekst.

        Deze toets is rood op de toestand van vóór de fix — daar bevatte de
        melding `400 Client Error` en verder niets uit de body. Zonder deze
        tegen-pin zou een melding die alleen de status herhaalt er even
        geslaagd uitzien als een melding die de reden geeft.
        """
        store = _store(_Response(400, IDENTITY_GEWEIGERD))

        melding = _melding_bij_schrijffout(store, "extraction_run", [{"entity": "customer"}])

        # De body, niet alleen de status. `raise_for_status()` gaf hier
        # "400 Client Error: Bad Request for url: ..." en verder niets.
        self.assertIn("non-DEFAULT value", melding)
        self.assertIn("428C9", melding)
        self.assertNotEqual(melding.strip().startswith("400 Client Error"), True)

    def test_de_melding_noemt_tabel_operatie_en_schema(self):
        store = _store(_Response(400, KOLOM_ONBEKEND))

        with self.assertRaises(StorageWriteError) as gevangen:
            store.append("extraction_run", [{"entity": "customer"}])

        melding = str(gevangen.exception)
        self.assertIn("append", melding)
        self.assertIn("simpul_raw.extraction_run", melding)
        self.assertEqual(gevangen.exception.table, "extraction_run")
        self.assertEqual(gevangen.exception.status, 400)

    def test_de_melding_draagt_nooit_de_api_sleutel(self):
        add_secrets(API_KEY)
        store = _store(_Response(401, '{"message":"No API key found ' + API_KEY + '"}'))

        with self.assertRaises(StorageWriteError) as gevangen:
            store.append("extraction_run", [{"entity": "customer"}])

        melding = str(gevangen.exception)
        self.assertNotIn(API_KEY, melding)
        self.assertIn(REDACTED, melding)

    def test_storage_write_error_is_een_storage_error(self):
        """`main()` vangt `StorageError`; een nieuwe fout die daar buiten valt
        zou als kale traceback naar buiten komen."""
        self.assertTrue(issubclass(StorageWriteError, StorageError))


class TestUpsertMeldtOokWaaromPostgrestWeigerde(unittest.TestCase):
    def test_upsert_draagt_dezelfde_reden(self):
        store = _store(_Response(400, KOLOM_ONBEKEND))

        melding = _melding_bij_schrijffout(store, "customer", [{"id": 1, "title": "Klant een"}])

        self.assertIn("PGRST204", melding)
        self.assertIn("upsert", melding)

    def test_geslaagde_schrijfactie_werpt_niets(self):
        store = _store(_Response(201, ""))

        resultaat = store.upsert("customer", [{"id": 1, "title": "Klant een"}])

        self.assertEqual(resultaat.inserted, 1)


if __name__ == "__main__":
    unittest.main()
