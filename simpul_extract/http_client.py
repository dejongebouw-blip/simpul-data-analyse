"""HTTP-laag naar Simpul.

Precies twee publieke uitgangen: `get()` voor elk pad, en `post()` die
uitsluitend `/login` accepteert en op elk ander pad een fout werpt. Er is
geen `put`, `patch` of `delete`, en geen doorgeefluik dat een willekeurige
methode aanroepbaar maakt.

De client neemt zijn transport (`session`) en zijn `sleep`-functie als
afhankelijkheid, zodat elke laag die hierop bouwt met een stub kan toetsen
zonder netwerk en zonder echte pauzes.
"""

import time

DEFAULT_DELAY = 0.3
MAX_ATTEMPTS = 4  # eerste poging + drie backoff-retries


class SimpulHTTPError(Exception):
    """Basisfout van de Simpul HTTP-laag."""


class ForbiddenMethodError(SimpulHTTPError):
    """Geworpen wanneer een niet-toegestaan schrijf-oppervlak wordt aangeroepen."""


class RetryExhaustedError(SimpulHTTPError):
    """Geworpen wanneer backoff is uitgeput zonder geslaagd verzoek."""


class SimpulHTTPClient:
    """Seriële, backoff-vaste HTTP-toegang tot Simpul."""

    def __init__(self, session, base_url="", delay=DEFAULT_DELAY, sleep=time.sleep,
                 max_attempts=MAX_ATTEMPTS):
        self._session = session
        self._base_url = base_url
        self.delay = delay
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._has_requested = False

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def post(self, path, data=None):
        if path != "/login":
            raise ForbiddenMethodError(
                f"POST is niet toegestaan naar {path!r}; alleen /login accepteert POST."
            )
        return self._request("POST", path, data=data)

    def _request(self, method, path, params=None, data=None):
        if self._has_requested:
            self._sleep(self.delay)
        self._has_requested = True

        url = self._base_url + path
        attempt = 0
        while True:
            attempt += 1
            response = self._session.request(method, url, params=params, data=data)
            status = response.status_code
            if status == 429 or 500 <= status < 600:
                if attempt >= self._max_attempts:
                    raise RetryExhaustedError(
                        f"{method} {path} bleef falen met status {status} na "
                        f"{attempt} pogingen; HTTP-laag stopt."
                    )
                self._sleep(self.delay * (2 ** (attempt - 1)))
                continue
            return response
