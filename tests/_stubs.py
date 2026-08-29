"""Netwerkloze stubs voor de HTTP-laag testen: geen enkel verzoek verlaat het proces."""

import json


class StubResponse:
    def __init__(self, status_code, body="", headers=None, set_cookies=None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {}
        # Cookies die een echte requests.Session automatisch uit de
        # Set-Cookie-header in zijn cookiejar zou overnemen na dit antwoord.
        self.set_cookies = set_cookies or {}

    def json(self):
        """Zoals requests.Response.json(): parseert `text` als JSON."""
        return json.loads(self.text)


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


class PagedSession(StubSession):
    """Serveert vooraf gebouwde pagineerpagina's (Fractal of Laravel) op
    ``?page=N``, voor pagineringstoetsen (issue 05). Net als `StubSession`
    registreert ze elk verzoek zonder netwerk aan te raken."""

    def __init__(self, pages, path=None):
        super().__init__()
        self._pages = dict(pages)
        self._path = path

    def request(self, method, url, params=None, data=None):
        query = dict(params or {})
        self.calls.append({"method": method, "url": url, "params": query, "data": data})
        if self._path is not None and self._path not in url:
            raise AssertionError(f"onverwacht pad: {url}; verwacht {self._path}")
        page = int(query.get("page", 1))
        if page not in self._pages:
            raise AssertionError(
                f"onverwachte paginavraag: page={page}; beschikbaar: {sorted(self._pages)}"
            )
        response = StubResponse(200, json.dumps(self._pages[page]))
        self.cookies.update(response.set_cookies)
        return response

    def requested_pages(self):
        return [int(call["params"].get("page", 1)) for call in self.calls]


def fractal_page(*, data, current_page, total_pages, total, per_page=50):
    return {
        "data": list(data),
        "meta": {
            "pagination": {
                "total": total,
                "count": len(data),
                "per_page": per_page,
                "current_page": current_page,
                "total_pages": total_pages,
            }
        },
    }


def laravel_page(*, data, current_page, last_page, total, per_page=50):
    return {
        "current_page": current_page,
        "data": list(data),
        "last_page": last_page,
        "per_page": per_page,
        "total": total,
    }


def _chunked_pages(rows, *, total, per_page, build_page):
    chunks = [rows[i : i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    last = len(chunks)
    claimed_total = len(rows) if total is None else total
    return {
        number: build_page(chunk, current_page=number, last=last, total=claimed_total)
        for number, chunk in enumerate(chunks, start=1)
    }


def fractal_rows_pages(row_count, *, total=None, per_page=50, start_id=1):
    """Fractal-paginareeks (``meta.pagination``) met ``row_count`` rijen,
    elk enkel een ``id`` (veldnaam-onafhankelijk, zoals deze naad vereist)."""
    rows = [{"id": i} for i in range(start_id, start_id + row_count)]
    return _chunked_pages(
        rows,
        total=total,
        per_page=per_page,
        build_page=lambda chunk, current_page, last, total: fractal_page(
            data=chunk, current_page=current_page, total_pages=last, total=total, per_page=per_page
        ),
    )


def laravel_rows_pages(row_count, *, total=None, per_page=50, start_id=1):
    """Laravel-paginatorreeks met ``row_count`` rijen, elk enkel een ``id``."""
    rows = [{"id": i} for i in range(start_id, start_id + row_count)]
    return _chunked_pages(
        rows,
        total=total,
        per_page=per_page,
        build_page=lambda chunk, current_page, last, total: laravel_page(
            data=chunk, current_page=current_page, last_page=last, total=total, per_page=per_page
        ),
    )


def synthetic_customer_record(**overrides):
    """Eén record zoals `/customer/all.json` het levert (issue 06 —
    veldnamen letterlijk uit de inventaris, waarden verzonnen)."""
    record = {
        "id": 32475,
        "customer_number": "K-32475",
        "title": "Voorbeeld Groenvoorziening B.V.",
        "address": "Voorbeeldstraat 1",
        "zipcode": "1234 AB",
        "city": "Voorbeeldstad",
        "phone": "010-1234567",
        "mobile": "06-12345678",
        "display_status": "actief",
        "tasks_status": "geen",
        "url_show": "/customer/32475",
    }
    record.update(overrides)
    return record


def synthetic_project_record(customer_overrides=None, drop_customer_fields=(), **overrides):
    """Eén record zoals `/project/all.json` het levert, inclusief het geneste
    `customer`-object (issue 06)."""
    customer = {
        "title": "Voorbeeld Groenvoorziening B.V.",
        "address": "Voorbeeldstraat 1",
        "zipcode": "1234 AB",
        "city": "Voorbeeldstad",
        "phone": "010-1234567",
        "mobile": "06-12345678",
    }
    for field in drop_customer_fields:
        customer.pop(field, None)
    if customer_overrides:
        customer.update(customer_overrides)
    record = {
        "id": 77001,
        "project_number": "P-77001",
        "name": "Herinrichting tuin",
        "status_id": 2,
        "url_show": "/project/77001",
        "invoiceable_amount": "1234.56",
        "project_location": "Voorbeeldstraat 1, Voorbeeldstad",
        "customer": customer,
    }
    record.update(overrides)
    return record


def synthetic_supplier_record(**overrides):
    """Eén record zoals `/supplier.json` het levert (issue 06)."""
    record = {
        "id": 501,
        "name": "Groenleverancier B.V.",
        "zipcode": "5678 CD",
        "city": "Andersstad",
        "email": "info@groenleverancier.example",
        "phone": "020-7654321",
        "mobile": "06-87654321",
        "address": "Leverancierslaan 9",
        "url_show": "/supplier/501",
        "text": "Vaste leverancier plantmateriaal.",
    }
    record.update(overrides)
    return record


def make_paginate_client(pages, path=None):
    """Bouwt een SimpulHTTPClient bovenop een `PagedSession`, zonder netwerk
    en zonder echte pauzes."""
    from simpul_extract.http_client import SimpulHTTPClient

    session = PagedSession(pages, path=path)
    client = SimpulHTTPClient(session, delay=0.0, sleep=lambda seconds: None)
    return client, session
