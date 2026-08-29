"""Toetst dat de HTTP-laag precies twee uitgangen kent: GET voor alles, en
één POST die uitsluitend /login accepteert. Geen enkele test doet een echt
netwerkverzoek; alles draait op tests._stubs.StubSession."""

import unittest

from simpul_extract.http_client import ForbiddenMethodError, SimpulHTTPClient
from tests._stubs import StubSession


class TestHttpReadonly(unittest.TestCase):
    def setUp(self):
        self.session = StubSession()
        self.client = SimpulHTTPClient(self.session, sleep=lambda seconds: None)

    def test_precies_twee_publieke_uitgangen(self):
        publieke_methoden = {
            naam for naam in dir(SimpulHTTPClient)
            if not naam.startswith("_") and callable(getattr(SimpulHTTPClient, naam))
        }
        self.assertEqual(publieke_methoden, {"get", "post"})

    def test_geen_put_patch_delete_methode(self):
        for verboden in ("put", "patch", "delete"):
            self.assertFalse(
                hasattr(self.client, verboden),
                f"client heeft een {verboden}-methode; die mag niet bestaan",
            )

    def test_get_is_toegestaan_op_elk_pad(self):
        self.client.get("/customer/1")
        self.assertEqual(self.session.calls[0]["method"], "GET")
        self.assertEqual(self.session.calls[0]["url"], "/customer/1")

    def test_post_naar_login_is_toegestaan(self):
        self.client.post("/login", data={"gebruiker": "x"})
        self.assertEqual(self.session.calls[0]["method"], "POST")
        self.assertEqual(self.session.calls[0]["url"], "/login")

    def test_post_naar_ander_pad_dan_login_werpt_fout(self):
        """Tegen-pin: een POST naar /customer/1 moet falen. Verwijder de
        padcontrole in http_client.py en deze test wordt rood."""
        with self.assertRaises(ForbiddenMethodError):
            self.client.post("/customer/1", data={"iets": "waarde"})
        self.assertEqual(
            self.session.calls, [],
            "de verboden POST heeft de sessie bereikt; hij had geblokkeerd moeten worden",
        )


if __name__ == "__main__":
    unittest.main()
