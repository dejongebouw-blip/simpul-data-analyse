"""Toetst seriële belasting (pauze tussen verzoeken) en exponentiële backoff
op 429/5xx met een maximum van drie retries. Alles draait op
tests._stubs.StubSession/RecordingSleep; er is geen echt netwerkverzoek en
geen echte pauze."""

import unittest

from simpul_extract.http_client import RetryExhaustedError, SimpulHTTPClient
from tests._stubs import RecordingSleep, StubResponse, StubSession


class TestHttpBackoff(unittest.TestCase):
    def test_standaard_delay_is_0_3_seconde(self):
        client = SimpulHTTPClient(StubSession())
        self.assertEqual(client.delay, 0.3)

    def test_delay_is_instelbaar(self):
        session = StubSession()
        sleep = RecordingSleep()
        client = SimpulHTTPClient(session, delay=1.5, sleep=sleep)

        client.get("/customer/1")
        client.get("/customer/2")

        self.assertIn(1.5, sleep.calls)

    def test_verzoeken_zijn_serieel_met_pauze_ertussen(self):
        session = StubSession()
        sleep = RecordingSleep()
        client = SimpulHTTPClient(session, delay=0.3, sleep=sleep)

        client.get("/customer/1")
        client.get("/customer/2")

        self.assertEqual(len(session.calls), 2)
        self.assertIn(0.3, sleep.calls)

    def test_retry_op_429_en_5xx_slaagt_binnen_max_pogingen(self):
        session = StubSession(responses=[
            StubResponse(429),
            StubResponse(503),
            StubResponse(200, "ok"),
        ])
        sleep = RecordingSleep()
        client = SimpulHTTPClient(session, delay=0.3, sleep=sleep)

        response = client.get("/customer/1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(len(sleep.calls), 2)
        self.assertLess(sleep.calls[0], sleep.calls[1])

    def test_stopt_na_vier_pogingen_met_expliciete_melding(self):
        session = StubSession(responses=[
            StubResponse(500),
            StubResponse(500),
            StubResponse(500),
            StubResponse(500),
            StubResponse(500),
        ])
        sleep = RecordingSleep()
        client = SimpulHTTPClient(session, delay=0.3, sleep=sleep)

        with self.assertRaises(RetryExhaustedError) as ctx:
            client.get("/customer/1")

        self.assertEqual(len(session.calls), 4, "moet stoppen na de vierde poging")
        self.assertIn("/customer/1", str(ctx.exception))
        self.assertIn("500", str(ctx.exception))

    def test_geen_parallelle_uitvoering_alleen_seriele_aanroepen(self):
        session = StubSession()
        sleep = RecordingSleep()
        client = SimpulHTTPClient(session, delay=0.3, sleep=sleep)

        for pad in ("/a", "/b", "/c"):
            client.get(pad)

        self.assertEqual([c["url"] for c in session.calls], ["/a", "/b", "/c"])
        self.assertEqual(sleep.calls, [0.3, 0.3])


if __name__ == "__main__":
    unittest.main()
