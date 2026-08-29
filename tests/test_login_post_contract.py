"""Toetst de POST-body van de login en de diagnose bij een mislukking.

De veldnamen zijn met de hand overgetypt uit de echte `GET /login` van de bron
(2026-08-29): `_token`, `username`, `password`, `remember`. Ze staan hier
letterlijk, niet geimporteerd uit `session.py` — anders pint de toets zichzelf
en niet de bron. Een eerdere versie postte `email` in plaats van `username`;
geen enkele test zag dat, omdat geen enkele test de body inspecteerde.

Alles draait op tests._stubs: geen netwerk, geen echte login.
"""

import unittest

from simpul_extract.http_client import SimpulHTTPClient
from simpul_extract.session import (
    SessionLostError,
    SessionRound,
    describe_response,
)
from tests._stubs import StubCookiePot, StubResponse, StubSession

# Letterlijk uit het formulier van de bron overgetypt.
FORM_CSRF_FIELD = "_token"
FORM_USERNAME_FIELD = "username"
FORM_PASSWORD_FIELD = "password"
FORM_REMEMBER_FIELD = "remember"
FORM_REMEMBER_VALUE = "on"

GEBRUIKERSNAAM = "een-gebruiker"
GEHEIM_WACHTWOORD = "correct-horse-battery-staple"
CSRF_WAARDE = "csrf-abc123"

INITIAL_COOKIES = {
    "__Host-s": "oude-s-waarde",
    "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d": "geheime-remember-waarde",
    "XSRF-TOKEN": "oude-xsrf-waarde",
}

LOGIN_PAGE_HTML = (
    '<form class="form-signin" method="POST" action="/login">'
    f'<input type="hidden" name="_token" value="{CSRF_WAARDE}" autocomplete="off">'
    '<input type="text" name="username" value="" id="inputUsername">'
    '<input type="password" name="password" id="inputPassword">'
    '<input type="checkbox" name="remember" id="remembercheck" value="on">'
    '</form>'
)


def _ronde(*, login_post_response, tweede_probe_response):
    """Bouwt een ronde met een dode sessie, zodat er precies eenmaal wordt
    ingelogd met de meegegeven uitkomst."""
    session = StubSession(responses=[
        StubResponse(302, headers={"Location": "/login"}),  # eerste probe: dood
        StubResponse(200, body=LOGIN_PAGE_HTML),            # GET /login
        login_post_response,                                # POST /login
        tweede_probe_response,                              # probe na de login
    ])
    client = SimpulHTTPClient(session, sleep=lambda s: None)
    pot = StubCookiePot(initial=INITIAL_COOKIES)
    round_ = SessionRound(client, session, pot, GEBRUIKERSNAAM, GEHEIM_WACHTWOORD)
    round_.start()
    return round_, session


def _geslaagde_ronde():
    return _ronde(
        login_post_response=StubResponse(302, headers={"Location": "/customer"}),
        tweede_probe_response=StubResponse(200, body="[]", headers={"Content-Type": "application/json"}),
    )


def _mislukte_ronde():
    return _ronde(
        login_post_response=StubResponse(
            302, headers={"Location": "https://bron.example/login", "Content-Type": "text/html; charset=utf-8"}
        ),
        tweede_probe_response=StubResponse(
            302, headers={"Location": "/login", "Content-Type": "text/html; charset=utf-8"}
        ),
    )


def _login_post(session):
    posts = [c for c in session.calls if c["method"] == "POST" and c["url"].endswith("/login")]
    assert len(posts) == 1, f"verwacht precies een POST /login, kreeg {len(posts)}"
    return posts[0]["data"]


class TestLoginPostBody(unittest.TestCase):
    def test_de_gebruikersnaam_gaat_in_het_veld_username(self):
        round_, session = _geslaagde_ronde()
        round_.ensure_live("/customer/all.json")

        body = _login_post(session)
        self.assertEqual(body.get(FORM_USERNAME_FIELD), GEBRUIKERSNAAM)

    def test_er_gaat_geen_veld_email_mee(self):
        """Het formulier van de bron kent geen `email`; Laravel zou de login
        weigeren omdat de gebruikersnaam ontbreekt."""
        round_, session = _geslaagde_ronde()
        round_.ensure_live("/customer/all.json")

        self.assertNotIn("email", _login_post(session))

    def test_het_wachtwoord_gaat_in_het_veld_password(self):
        round_, session = _geslaagde_ronde()
        round_.ensure_live("/customer/all.json")

        self.assertEqual(_login_post(session).get(FORM_PASSWORD_FIELD), GEHEIM_WACHTWOORD)

    def test_het_csrf_token_van_de_loginpagina_gaat_mee(self):
        round_, session = _geslaagde_ronde()
        round_.ensure_live("/customer/all.json")

        self.assertEqual(_login_post(session).get(FORM_CSRF_FIELD), CSRF_WAARDE)

    def test_remember_gaat_mee_zodat_laravel_de_remember_cookie_zet(self):
        """De pot draagt `remember_web_*` (PRD, issue 04). Zonder het vinkje
        zet Laravel die cookie nooit en is de pot na elke ronde armer."""
        round_, session = _geslaagde_ronde()
        round_.ensure_live("/customer/all.json")

        self.assertEqual(_login_post(session).get(FORM_REMEMBER_FIELD), FORM_REMEMBER_VALUE)

    def test_de_body_draagt_precies_de_vier_formuliervelden(self):
        round_, session = _geslaagde_ronde()
        round_.ensure_live("/customer/all.json")

        self.assertEqual(
            set(_login_post(session)),
            {FORM_CSRF_FIELD, FORM_USERNAME_FIELD, FORM_PASSWORD_FIELD, FORM_REMEMBER_FIELD},
        )


class TestLoginFailureDiagnose(unittest.TestCase):
    def test_de_melding_noemt_de_status_van_de_login_post(self):
        round_, _ = _mislukte_ronde()

        with self.assertRaises(SessionLostError) as ctx:
            round_.ensure_live("/customer/all.json")

        self.assertIn("POST /login", str(ctx.exception))
        self.assertIn("status 302", str(ctx.exception))

    def test_de_melding_noemt_de_location_en_het_content_type(self):
        round_, _ = _mislukte_ronde()

        with self.assertRaises(SessionLostError) as ctx:
            round_.ensure_live("/customer/all.json")

        melding = str(ctx.exception)
        self.assertIn("/login", melding)
        self.assertIn("text/html", melding)

    def test_de_melding_noemt_de_probe_na_de_login(self):
        round_, _ = _mislukte_ronde()

        with self.assertRaises(SessionLostError) as ctx:
            round_.ensure_live("/customer/all.json")

        self.assertIn("/customer/all.json", str(ctx.exception))

    def test_de_melding_draagt_geen_wachtwoord_en_geen_cookiewaarde(self):
        round_, _ = _mislukte_ronde()

        with self.assertRaises(SessionLostError) as ctx:
            round_.ensure_live("/customer/all.json")

        melding = str(ctx.exception)
        self.assertNotIn(GEHEIM_WACHTWOORD, melding)
        for waarde in INITIAL_COOKIES.values():
            self.assertNotIn(waarde, melding)

    def test_de_melding_draagt_geen_body_van_de_respons(self):
        """Een loginpagina kan een vers CSRF-token dragen; dat hoort niet in
        een foutmelding."""
        round_, _ = _ronde(
            login_post_response=StubResponse(
                200, body=LOGIN_PAGE_HTML, headers={"Content-Type": "text/html"}
            ),
            tweede_probe_response=StubResponse(302, headers={"Location": "/login"}),
        )

        with self.assertRaises(SessionLostError) as ctx:
            round_.ensure_live("/customer/all.json")

        self.assertNotIn(CSRF_WAARDE, str(ctx.exception))


class TestDescribeResponse(unittest.TestCase):
    def test_een_gevolgde_redirect_wordt_zichtbaar_via_de_history(self):
        """requests volgt de redirect van een geslaagde login; dan is de
        eerste hop het enige bewijs van wat de bron antwoordde."""
        eerste_hop = StubResponse(302, headers={"Location": "/login"})
        eind = StubResponse(200, headers={"Content-Type": "text/html"})
        eind.history = [eerste_hop]

        beschrijving = describe_response(eind)

        self.assertIn("status 200", beschrijving)
        self.assertIn("via 302", beschrijving)
        self.assertIn("/login", beschrijving)

    def test_geen_respons_levert_een_leesbare_beschrijving_op(self):
        self.assertEqual(describe_response(None), "geen respons")


if __name__ == "__main__":
    unittest.main()


class TestPotDirectNaDeLogin(unittest.TestCase):
    """De sessie is duur (één loginpoging per ronde). Ze wordt daarom meteen
    na een geslaagde login vastgelegd, niet pas bij finish() aan het eind van
    een ronde die een half uur duurt en pas op het laatst wegschrijft."""

    def test_een_geslaagde_login_schrijft_de_pot_meteen(self):
        round_, session = _ronde(
            login_post_response=StubResponse(
                302, headers={"Location": "/customer"},
                set_cookies={"__Host-s": "verse-s-waarde"},
            ),
            tweede_probe_response=StubResponse(
                200, body="[]", headers={"Content-Type": "application/json"}
            ),
        )
        pot = round_._pot

        round_.ensure_live("/customer/all.json")

        self.assertEqual(
            len(pot.write_calls), 1,
            "de pot moet geschreven zijn zodra de login slaagde, niet pas bij finish()",
        )
        self.assertEqual(pot.write_calls[0].get("__Host-s"), "verse-s-waarde")

    def test_een_levende_sessie_schrijft_niet_tussentijds(self):
        """Zonder login is er niets nieuws vast te leggen; finish() doet de
        rotatie aan het eind."""
        session = StubSession(responses=[
            StubResponse(200, body="[]", headers={"Content-Type": "application/json"}),
        ])
        client = SimpulHTTPClient(session, sleep=lambda s: None)
        pot = StubCookiePot(initial=INITIAL_COOKIES)
        round_ = SessionRound(client, session, pot, GEBRUIKERSNAAM, GEHEIM_WACHTWOORD)
        round_.start()

        round_.ensure_live("/customer/all.json")

        self.assertEqual(pot.write_calls, [])

    def test_een_mislukte_login_schrijft_nog_steeds_niets(self):
        """De bestaande grens blijft staan: een dode sessie raakt de pot niet."""
        round_, _ = _mislukte_ronde()
        pot = round_._pot

        with self.assertRaises(SessionLostError):
            round_.ensure_live("/customer/all.json")

        self.assertEqual(pot.write_calls, [])
