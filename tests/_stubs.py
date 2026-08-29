"""Netwerkloze stubs voor de HTTP-laag testen: geen enkel verzoek verlaat het proces."""


class StubResponse:
    def __init__(self, status_code, body="", headers=None, set_cookies=None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {}
        # Cookies die een echte requests.Session automatisch uit de
        # Set-Cookie-header in zijn cookiejar zou overnemen na dit antwoord.
        self.set_cookies = set_cookies or {}


class StubSession:
    """Vervangt de echte HTTP-sessie. Geeft vooraf ingestelde responses terug
    en registreert elk verzoek, zodat een test kan bewijzen dat er geen
    verzoek naar een verboden pad is uitgevoerd. Houdt net als een echte
    requests.Session een cookiejar (`.cookies`) bij: verzoeken kunnen die
    lezen/vullen, en de `set_cookies` van een response wordt er na afloop in
    overgenomen."""

    def __init__(self, responses=None, cookies=None):
        self._responses = list(responses) if responses is not None else []
        self.calls = []
        self.cookies = dict(cookies) if cookies else {}

    def request(self, method, url, params=None, data=None):
        self.calls.append({"method": method, "url": url, "params": params, "data": data})
        if self._responses:
            response = self._responses.pop(0)
        else:
            response = StubResponse(200)
        self.cookies.update(response.set_cookies)
        return response


class RecordingSleep:
    """Vervangt time.sleep: registreert de pauzes zonder ze uit te voeren."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


class StubCookiePot:
    """Injecteerbare cookiepot voor tests: een in-memory vervanging van de
    echte pot (`simpul_raw.session_cookie`, buiten scope van deze toets).
    Registreert elke write() zodat een test kan bewijzen dat er bij een dode
    sessie geen enkele schrijfactie plaatsvond."""

    def __init__(self, initial=None):
        self._cookies = dict(initial) if initial else {}
        self.write_calls = []

    def read(self):
        return dict(self._cookies)

    def write(self, cookies):
        self.write_calls.append(dict(cookies))
        self._cookies = dict(cookies)
