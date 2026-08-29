"""Toetst dat een 429 of 5xx een spoor achterlaat, ook als hij daarna slaagt.

H7 uit het testplan vraagt dat de ronde de bron aantoonbaar niet belast:
serieel, met pauze, geen 429. De eerste twee zijn deterministisch aangetoond
(Q4). Het derde was dat niet: `_request` ving een 429 op, sliep en probeerde
opnieuw zónder er iets over te zeggen. Alleen een 429 die vier pogingen lang
bleef staan werd zichtbaar, en dan als exceptie.

Een schone log bewees daardoor niet "geen 429", maar alleen "geen 429 die vier
keer op rij terugkwam" — een veel zwakkere claim dan H7 vraagt. Deze toets pint
vast dat elke backoff-ronde zich meldt.

Netwerkloos: elke sessie is een stub, er verlaat geen verzoek dit proces.
"""
from __future__ import annotations

import io
import unittest

from simpul_extract.http_client import RetryExhaustedError, SimpulHTTPClient
from simpul_extract.observability import configure_logging
from tests._stubs import RecordingSleep, StubResponse, StubSession


def _client_met_log(responses):
    stroom = io.StringIO()
    configure_logging({"LOG_LEVEL": "INFO"}, stream=stroom)
    client = SimpulHTTPClient(
        StubSession(responses), base_url="https://bron.invalid",
        sleep=RecordingSleep(),
    )
    return client, stroom


class TestEenOpgevangen429LaatEenSpoorNa(unittest.TestCase):
    def test_429_gevolgd_door_200_meldt_zich(self):
        client, stroom = _client_met_log([StubResponse(429), StubResponse(200)])

        response = client.get("/customer/all.json")

        self.assertEqual(response.status_code, 200)
        uitvoer = stroom.getvalue()
        self.assertIn("429", uitvoer)
        self.assertIn("/customer/all.json", uitvoer)
        self.assertIn("poging 1 van 4", uitvoer)

    def test_tegenpin_een_ronde_zonder_429_meldt_niets(self):
        """Tegen-pin: de regel moet uit de 429 komen, niet uit elk verzoek.

        Zou de client bij elk verzoek een waarschuwing schrijven, dan zei een
        volle log niets meer over de belasting van de bron. Deze toets is rood
        zodra de waarschuwing losstaat van de statuscode.
        """
        client, stroom = _client_met_log([StubResponse(200)])

        client.get("/customer/all.json")

        self.assertNotIn("WARNING", stroom.getvalue())

    def test_elke_backoff_ronde_meldt_zich_apart(self):
        client, stroom = _client_met_log(
            [StubResponse(429), StubResponse(503), StubResponse(200)]
        )

        client.get("/project/all.json")

        uitvoer = stroom.getvalue()
        self.assertIn("429", uitvoer)
        self.assertIn("503", uitvoer)
        self.assertEqual(uitvoer.count("WARNING"), 2)

    def test_uitgeputte_backoff_logt_op_error_niveau(self):
        client, stroom = _client_met_log([StubResponse(500)] * 4)

        with self.assertRaises(RetryExhaustedError):
            client.get("/supplier.json")

        uitvoer = stroom.getvalue()
        self.assertIn("ERROR", uitvoer)
        self.assertIn("backoff uitgeput", uitvoer)

    def test_de_gemelde_pauze_verdubbelt(self):
        client, stroom = _client_met_log(
            [StubResponse(429), StubResponse(429), StubResponse(200)]
        )
        client.delay = 1.0

        client.get("/customer/all.json")

        uitvoer = stroom.getvalue()
        self.assertIn("1.0s backoff", uitvoer)
        self.assertIn("2.0s backoff", uitvoer)


if __name__ == "__main__":
    unittest.main()
