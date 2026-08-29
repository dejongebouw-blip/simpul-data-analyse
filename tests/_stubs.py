"""Netwerkloze stubs voor de HTTP-laag testen: geen enkel verzoek verlaat het proces."""


class StubResponse:
    def __init__(self, status_code, body=""):
        self.status_code = status_code
        self.text = body


class StubSession:
    """Vervangt de echte HTTP-sessie. Geeft vooraf ingestelde responses terug
    en registreert elk verzoek, zodat een test kan bewijzen dat er geen
    verzoek naar een verboden pad is uitgevoerd."""

    def __init__(self, responses=None):
        self._responses = list(responses) if responses is not None else []
        self.calls = []

    def request(self, method, url, params=None, data=None):
        self.calls.append({"method": method, "url": url, "params": params, "data": data})
        if self._responses:
            return self._responses.pop(0)
        return StubResponse(200)


class RecordingSleep:
    """Vervangt time.sleep: registreert de pauzes zonder ze uit te voeren."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
