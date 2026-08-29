"""Toetst het faalpad: een sessie die ook na de ene toegestane loginpoging
blijft weigeren levert exit code 2 op, zonder tweede poging en zonder dat de
schrijflaag (de cookiepot) ook maar één keer is aangeroepen. Toetst daarnaast
dat geen cookiewaarde en geen wachtwoord in stdout, stderr of een foutmelding
terechtkomt. Alles draait op tests._stubs; er is geen echt netwerkverzoek en
geen echte login."""

import contextlib
import io
import unittest

from simpul_extract.http_client import SimpulHTTPClient
from simpul_extract.session import (
    EXIT_SESSION_LOST,
    SessionLostError,
    SessionRound,
    run_round,
)
from tests._stubs import StubCookiePot, StubResponse, StubSession

INITIAL_COOKIES = {
    "__Host-s": "oude-s-waarde",
    "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d": "geheime-remember-waarde",
    "XSRF-TOKEN": "oude-xsrf-waarde",
}

GEHEIM_WACHTWOORD = "correct-horse-battery-staple"

LOGIN_PAGE_HTML = '<form><input type="hidden" name="_token" value="csrf-abc123"></form>'


def _dode_sessie_stub():
    return StubSession(responses=[
        StubResponse(302, headers={"Location": "/login"}),  # eerste probe: dood
        StubResponse(200, body=LOGIN_PAGE_HTML),             # GET /login
        StubResponse(302, headers={"Location": "/login"}),   # POST /login: geweigerd
        StubResponse(302, headers={"Location": "/login"}),   # tweede probe: nog steeds dood
    ])


def _count_login_posts(session):
    return len([
        call for call in session.calls
        if call["method"] == "POST" and call["url"] == "/login"
    ])


class TestSessionFailure(unittest.TestCase):
    def test_dode_sessie_na_login_levert_exit_code_2_op(self):
        session = _dode_sessie_stub()
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)

        exit_code = run_round(client, session, pot, "/api/dashboard", "gebruiker", GEHEIM_WACHTWOORD)

        self.assertEqual(exit_code, EXIT_SESSION_LOST)

    def test_dode_sessie_na_login_schrijft_nul_keer_naar_de_pot(self):
        session = _dode_sessie_stub()
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)

        run_round(client, session, pot, "/api/dashboard", "gebruiker", GEHEIM_WACHTWOORD)

        self.assertEqual(pot.write_calls, [], "de schrijflaag mag nul keer zijn aangeroepen")

    def test_precies_een_loginpoging_geen_tweede_binnen_de_ronde(self):
        session = _dode_sessie_stub()
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)

        run_round(client, session, pot, "/api/dashboard", "gebruiker", GEHEIM_WACHTWOORD)

        self.assertEqual(
            _count_login_posts(session), 1,
            "een dode sessie mag na de mislukte poging niet nog eens inloggen",
        )

    def test_tweede_ensure_live_na_mislukking_probeert_niet_opnieuw_in_te_loggen(self):
        session = _dode_sessie_stub()
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)
        round_ = SessionRound(client, session, pot, "gebruiker", GEHEIM_WACHTWOORD)
        round_.start()

        with self.assertRaises(SessionLostError):
            round_.ensure_live("/api/dashboard")

        # Een volgend verzoek binnen dezelfde ronde ziet nog steeds een dode
        # sessie (geen nieuwe stub-response ingesteld voor een tweede login).
        session._responses.append(StubResponse(302, headers={"Location": "/login"}))
        with self.assertRaises(SessionLostError):
            round_.ensure_live("/api/dashboard")

        self.assertEqual(_count_login_posts(session), 1)

    def test_geen_wachtwoord_of_cookiewaarde_in_stdout_of_stderr(self):
        session = _dode_sessie_stub()
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            run_round(client, session, pot, "/api/dashboard", "gebruiker", GEHEIM_WACHTWOORD)

        gelekt = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(GEHEIM_WACHTWOORD, gelekt)
        for naam, waarde in INITIAL_COOKIES.items():
            self.assertNotIn(waarde, gelekt)

    def test_geen_wachtwoord_of_cookiewaarde_in_de_foutmelding(self):
        session = _dode_sessie_stub()
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)
        round_ = SessionRound(client, session, pot, "gebruiker", GEHEIM_WACHTWOORD)
        round_.start()

        with self.assertRaises(SessionLostError) as ctx:
            round_.ensure_live("/api/dashboard")

        melding = str(ctx.exception)
        self.assertNotIn(GEHEIM_WACHTWOORD, melding)
        for naam, waarde in INITIAL_COOKIES.items():
            self.assertNotIn(waarde, melding)


if __name__ == "__main__":
    unittest.main()
