"""Toetst sessieherstel: de ronde leest de cookiepot bij de start, herkent
sessieverlies (een redirect naar /login, of een 200 met text/html waar JSON
verwacht wordt) en lokt daarbij precies één loginpoging uit — niet nul, niet
twee — en schrijft de door Laravel geroteerde cookiewaarde terug bij afloop.
Alles draait op tests._stubs; er is geen echt netwerkverzoek."""

import unittest

from simpul_extract.http_client import SimpulHTTPClient
from simpul_extract.session import (
    EXIT_OK,
    SessionRound,
    run_round,
    session_is_lost,
)
from tests._stubs import StubCookiePot, StubResponse, StubSession

INITIAL_COOKIES = {
    "__Host-s": "oude-s-waarde",
    "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d": "oude-remember-waarde",
    "XSRF-TOKEN": "oude-xsrf-waarde",
}

ROTATED_COOKIES = {
    "__Host-s": "nieuwe-s-waarde",
    "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d": "nieuwe-remember-waarde",
    "XSRF-TOKEN": "nieuwe-xsrf-waarde",
}

LOGIN_PAGE_HTML = '<form><input type="hidden" name="_token" value="csrf-abc123"></form>'


def _count_login_posts(session):
    return len([
        call for call in session.calls
        if call["method"] == "POST" and call["url"] == "/login"
    ])


class TestSessionRecovery(unittest.TestCase):
    def test_start_leest_pot_en_zet_cookies_in_de_sessie(self):
        session = StubSession()
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)
        round_ = SessionRound(client, session, pot, "gebruiker", "wachtwoord")

        round_.start()

        self.assertEqual(session.cookies["__Host-s"], "oude-s-waarde")
        self.assertEqual(session.cookies["XSRF-TOKEN"], "oude-xsrf-waarde")

    def test_302_naar_login_lokt_precies_een_loginpoging_uit(self):
        session = StubSession(responses=[
            StubResponse(302, headers={"Location": "https://simpul.example/login"}),
            StubResponse(200, body=LOGIN_PAGE_HTML),
            StubResponse(302, headers={"Location": "/dashboard"}, set_cookies=ROTATED_COOKIES),
            StubResponse(200, body='{"ok": true}', headers={"Content-Type": "application/json"}),
        ])
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)

        exit_code = run_round(client, session, pot, "/api/dashboard", "gebruiker", "wachtwoord")

        self.assertEqual(exit_code, EXIT_OK)
        self.assertEqual(
            _count_login_posts(session), 1,
            "een dode sessie moet precies één loginpoging uitlokken",
        )
        # Twee schrijfacties, allebei met de geroteerde cookies: één zodra de
        # login slaagde (de sessie is duur en mag niet verloren gaan als de
        # ronde later omvalt) en één bij finish(). Vóór 2026-08-29 was dit er
        # één, aan het eind; zie tests/test_login_post_contract.py.
        self.assertEqual(pot.write_calls, [ROTATED_COOKIES, ROTATED_COOKIES])
        self.assertEqual(pot.read(), ROTATED_COOKIES)

    def test_200_met_html_waar_json_verwacht_wordt_telt_als_sessieverlies(self):
        session = StubSession(responses=[
            StubResponse(200, body="<html>login</html>", headers={"Content-Type": "text/html; charset=UTF-8"}),
            StubResponse(200, body=LOGIN_PAGE_HTML),
            StubResponse(200, body="", set_cookies=ROTATED_COOKIES),
            StubResponse(200, body='{"ok": true}', headers={"Content-Type": "application/json"}),
        ])
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)

        exit_code = run_round(client, session, pot, "/api/dashboard", "gebruiker", "wachtwoord")

        self.assertEqual(exit_code, EXIT_OK)
        self.assertEqual(_count_login_posts(session), 1)

    def test_levende_sessie_lokt_geen_enkele_loginpoging_uit(self):
        session = StubSession(responses=[
            StubResponse(200, body='{"ok": true}', headers={"Content-Type": "application/json"}),
        ])
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)

        exit_code = run_round(client, session, pot, "/api/dashboard", "gebruiker", "wachtwoord")

        self.assertEqual(exit_code, EXIT_OK)
        self.assertEqual(_count_login_posts(session), 0)
        self.assertEqual(pot.write_calls, [INITIAL_COOKIES])

    def test_session_is_lost_herkent_redirect_en_html(self):
        self.assertTrue(session_is_lost(
            StubResponse(302, headers={"Location": "/login"})
        ))
        self.assertTrue(session_is_lost(
            StubResponse(200, headers={"Content-Type": "text/html"})
        ))
        self.assertFalse(session_is_lost(
            StubResponse(200, headers={"Content-Type": "application/json"})
        ))
        self.assertFalse(session_is_lost(
            StubResponse(302, headers={"Location": "/dashboard"})
        ))


if __name__ == "__main__":
    unittest.main()
