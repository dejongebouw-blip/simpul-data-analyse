"""Sessiebeheer voor Simpul (adr/2026-08-29-job-logt-zelf-in.md).

Volgorde per ronde:
  1. Lees de cookiepot bij de start en injecteer `__Host-s`, `remember_web_*`
     en `XSRF-TOKEN` in de HTTP-sessie.
  2. Bewijs met een verzoek dat de sessie leeft. Blijkt ze dood — een
     redirect naar /login, of een 200 met text/html waar JSON verwacht
     wordt — dan wordt er precies één keer ingelogd: GET /login voor het
     CSRF-token, POST /login met de credentials.
  3. Weigert de sessie ook na die ene poging, dan stopt de ronde met
     EXIT_SESSION_LOST en wordt er niets naar de pot geschreven. Er volgt
     geen tweede loginpoging binnen dezelfde ronde.
  4. Slaagt de ronde, dan gaat de (door Laravel mogelijk geroteerde)
     cookiewaarde terug naar de pot.

Geen cookiewaarde en geen wachtwoord wordt ooit in een foutmelding
geformatteerd.
"""

import re

from simpul_extract.observability import add_secrets, get_logger

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_SESSION_LOST = 2

SESSION_COOKIE_NAME = "__Host-s"
XSRF_COOKIE_NAME = "XSRF-TOKEN"
REMEMBER_COOKIE_PREFIX = "remember_web_"

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_CSRF_INPUT_RE = re.compile(r'name="_token"\s+value="([^"]*)"')

# De veldnamen van het loginformulier, met de hand overgetypt uit de echte
# `GET /login` van de bron (2026-08-29):
#
#   <input type="hidden" name="_token" ...>
#   <input type="text"     name="username" placeholder="Gebruikersnaam of e-mailadres">
#   <input type="password" name="password" ...>
#   <input type="checkbox" name="remember" value="on">
#
# Een eerdere versie postte `email` in plaats van `username`. Laravel zag dan
# geen gebruikersnaam, weigerde de login, en omdat `_login()` de respons
# negeerde bleef dat onzichtbaar: de ronde meldde alleen "sessie: dood".
CSRF_FIELD = "_token"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
REMEMBER_FIELD = "remember"
REMEMBER_VALUE = "on"


class SessionError(Exception):
    """Basisfout van sessiebeheer."""


class SessionLostError(SessionError):
    """De sessie is dood en kon niet worden hersteld via de ene toegestane
    loginpoging."""


class CookiePot:
    """Injecteerbare potinterface voor de sessiecookies.

    Concrete implementaties (bijv. tegen `simpul_raw.session_cookie`) en
    teststubs voldoen hieraan door `read()`/`write()` te implementeren; er
    is verder geen contractcontrole nodig omdat Python duck-typet.
    """

    def read(self):
        raise NotImplementedError

    def write(self, cookies):
        raise NotImplementedError


def _is_tracked_cookie(name):
    return (
        name == SESSION_COOKIE_NAME
        or name == XSRF_COOKIE_NAME
        or name.startswith(REMEMBER_COOKIE_PREFIX)
    )


def _header(response, name):
    headers = getattr(response, "headers", None) or {}
    return headers.get(name, "")


def session_is_lost(response):
    """True als `response` sessieverlies aantoont: een redirect naar /login,
    of een 200 met content-type text/html waar JSON verwacht wordt. Dat
    laatste telt expliciet als sessieverlies, niet als een lege pagina."""
    if response.status_code in _REDIRECT_STATUSES:
        return "/login" in _header(response, "Location")
    if response.status_code == 200:
        return "text/html" in _header(response, "Content-Type")
    return False


def describe_response(response):
    """Beschrijft een respons in termen die een faalmelding mag dragen:
    statuscode, `Location` en `Content-Type`. Nooit een body, nooit een
    cookiewaarde, nooit een header met een credential erin."""
    if response is None:
        return "geen respons"
    parts = [f"status {getattr(response, 'status_code', '?')}"]
    location = _header(response, "Location")
    if location:
        parts.append(f"Location {location}")
    content_type = _header(response, "Content-Type")
    if content_type:
        parts.append(f"content-type {content_type}")
    history = getattr(response, "history", None) or ()
    if history:
        first = history[0]
        parts.append(
            f"via {getattr(first, 'status_code', '?')} "
            f"{_header(first, 'Location') or 'zonder Location'}"
        )
    return ", ".join(parts)


def _apply_cookies(session, cookies):
    for name, value in cookies.items():
        session.cookies[name] = value
        # Elke cookiewaarde die dit proces binnenkomt wordt meteen als geheim
        # geregistreerd, zodat geen enkele logregel of foutmelding hem later
        # per ongeluk kan echoën. Namen mogen wel: die verklaren een fout.
        add_secrets(value)


def _read_tracked_cookies(session):
    return {
        name: value
        for name, value in dict(session.cookies).items()
        if _is_tracked_cookie(name)
    }


def _extract_csrf_token(login_page):
    match = _CSRF_INPUT_RE.search(login_page.text or "")
    if not match:
        raise SessionError("kon geen CSRF-token vinden op de loginpagina")
    return match.group(1)


def credentials_from_env(env):
    """Leest SIMPUL_USERNAME/SIMPUL_PASSWORD uit `env` (bijv. os.environ)."""
    username = env.get("SIMPUL_USERNAME")
    password = env.get("SIMPUL_PASSWORD")
    if not username or not password:
        raise SessionError(
            "SIMPUL_USERNAME en SIMPUL_PASSWORD zijn beide vereist om in te loggen"
        )
    return username, password


class SessionRound:
    """Beheert de sessie voor één ronde.

    `client` is een SimpulHTTPClient; `session` is de onderliggende
    HTTP-sessie waar `client` cookies op leest en zet.
    """

    def __init__(self, client, session, pot, username, password):
        self._client = client
        self._session = session
        self._pot = pot
        self._username = username
        self._password = password
        self._login_attempted = False
        self._started = False

    def start(self):
        """Leest de cookiepot en injecteert de cookies in de sessie."""
        cookies = self._pot.read()
        logger.info("cookiepot gelezen: %d cookies (%s)", len(cookies), sorted(cookies))
        _apply_cookies(self._session, cookies)
        self._started = True

    def _login(self):
        """Doet de ene toegestane loginpoging en geeft de POST-respons terug,
        zodat een mislukking te diagnosticeren is."""
        logger.info("sessie dood; de enige toegestane loginpoging van deze ronde")
        login_page = self._client.get("/login")
        token = _extract_csrf_token(login_page)
        return self._client.post("/login", data={
            CSRF_FIELD: token,
            USERNAME_FIELD: self._username,
            PASSWORD_FIELD: self._password,
            # De pot draagt `remember_web_*` (PRD en issue 04); Laravel zet die
            # cookie alleen als het formulier het vinkje meestuurt.
            REMEMBER_FIELD: REMEMBER_VALUE,
        })

    def ensure_live(self, probe_path):
        """Bewijst met een verzoek naar `probe_path` dat de sessie leeft.
        Blijkt ze dood, dan wordt precies één keer ingelogd. Blijft de
        sessie ook daarna dood, dan wordt SessionLostError geworpen."""
        if not self._started:
            raise SessionError("start() moet aangeroepen zijn voor ensure_live()")

        response = self._client.get(probe_path)
        if not session_is_lost(response):
            return response

        if self._login_attempted:
            raise SessionLostError(
                "sessie is dood; er is deze ronde al ingelogd, geen tweede poging"
            )
        self._login_attempted = True
        login_response = self._login()

        response = self._client.get(probe_path)
        if not session_is_lost(response):
            # De sessie is duur: één loginpoging per ronde
            # (adr/2026-08-29-job-logt-zelf-in.md). Leg haar meteen vast, niet
            # pas in finish(). Een ronde duurt een half uur tot drie kwartier
            # en schrijft de data pas aan het eind; valt ze daarvóór om, dan
            # was de zojuist gewonnen sessie anders weg en kostte de volgende
            # ronde opnieuw een login. Aangetoond op 2026-08-29: H6 crashte op
            # de auditregel en liet de pot leeg achter.
            cookies = _read_tracked_cookies(self._session)
            logger.info(
                "login geslaagd; pot meteen geschreven: %d cookies (%s)",
                len(cookies), sorted(cookies),
            )
            self._pot.write(cookies)
            return response

        raise SessionLostError(
            "sessie blijft dood na de enige toegestane loginpoging; "
            f"POST /login gaf {describe_response(login_response)}; "
            f"de probe {probe_path} daarna gaf {describe_response(response)}"
        )

    def finish(self):
        """Schrijft de (mogelijk geroteerde) cookiewaarde terug naar de pot."""
        cookies = _read_tracked_cookies(self._session)
        logger.info("ronde klaar; pot bijgewerkt: %d cookies (%s)", len(cookies), sorted(cookies))
        self._pot.write(cookies)


def run_round(client, session, pot, probe_path, username, password):
    """Voert de sessiestap van een ronde uit end-to-end.

    Retourneert EXIT_OK als de sessie leefde of via de ene toegestane login
    hersteld is — de pot is dan bijgewerkt. Retourneert EXIT_SESSION_LOST
    als die ene poging niet volstond — de pot is dan niet aangeraakt.
    """
    round_ = SessionRound(client, session, pot, username, password)
    round_.start()
    try:
        round_.ensure_live(probe_path)
    except SessionLostError:
        return EXIT_SESSION_LOST
    round_.finish()
    return EXIT_OK
