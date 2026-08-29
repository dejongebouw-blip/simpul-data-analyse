"""Toetst de volledige ronde (issue 10): de bedrading die de lagen uit
03 t/m 09 verbindt tot één `python3 -m simpul_extract`-aanroep over
`customer`, `project` en `supplier`.

Reproduceer eerst met één stubset die de hele ronde afdekt: drie entiteiten
met paginering in beide vormen (Fractal voor customer/project, Laravel voor
supplier), drie customer-detailpagina's waarvan één zonder `mailto:`-anker,
en een cookiepot met een geldige sessie. Zolang de bedrading in
`simpul_extract/__main__.py` ontbreekt, is er geen ronde die exit 0 haalt.

Toetst daarnaast de twee faalwegen op dezelfde stubset: een geweigerde login
(exit 2, nul upserts, pot ongemoeid) en een entiteit die minder rijen levert
dan de bron als totaal meldt (niet-nul, niet-2, cookie wél teruggeschreven).

Alles draait op `tests._stubs` en de in-memory nepimplementatie uit issue 07;
er gaat geen verzoek naar een draaiende omgeving en geen netwerk wordt
aangeraakt (de verify-runner bouwt en draait dit bovendien met
`--network none`).
"""
from __future__ import annotations

import io
import json
import unittest

from simpul_extract.__main__ import run
from simpul_extract.completeness import EXIT_INCOMPLETE, EXTRACTION_RUN_TABLE
from simpul_extract.session import EXIT_OK, EXIT_SESSION_LOST
from simpul_extract.storage import InMemoryUpsertStore
from tests._stubs import (
    StubCookiePot,
    StubResponse,
    customer_detail_html_with_mailto,
    customer_detail_html_without_mailto,
    fractal_page,
    laravel_page,
    synthetic_customer_record,
    synthetic_project_record,
    synthetic_supplier_record,
)

PROBE_PATH = "/api/dashboard"
LOGIN_PAGE_HTML = '<form><input type="hidden" name="_token" value="csrf-abc123"></form>'

INITIAL_COOKIES = {
    "__Host-s": "oude-s-waarde",
    "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d": "oude-remember-waarde",
    "XSRF-TOKEN": "oude-xsrf-waarde",
}

GEHEIM_WACHTWOORD = "correct-horse-battery-staple"

CUSTOMER_IDS = (101, 102, 103)
CUSTOMER_EMAILS = {
    101: "relatie101@voorbeeld.example",
    102: "relatie102@voorbeeld.example",
}


class _CountingClock:
    """Deterministische klok: elke aanroep levert een nieuwe, oplopende
    waarde, onafhankelijk van een echte klok."""

    def __init__(self):
        self._calls = 0

    def __call__(self):
        self._calls += 1
        return f"2026-08-29T00:00:{self._calls:02d}+00:00"


def _chunks(records, per_page):
    chunked = [records[i : i + per_page] for i in range(0, len(records), per_page)]
    return chunked or [[]]


def _fractal_pages(records, *, per_page, total=None):
    chunks = _chunks(records, per_page)
    last = len(chunks)
    claimed_total = len(records) if total is None else total
    return {
        n: fractal_page(data=chunk, current_page=n, total_pages=last, total=claimed_total, per_page=per_page)
        for n, chunk in enumerate(chunks, start=1)
    }


def _laravel_pages(records, *, per_page, total=None):
    chunks = _chunks(records, per_page)
    last = len(chunks)
    claimed_total = len(records) if total is None else total
    return {
        n: laravel_page(data=chunk, current_page=n, last_page=last, total=claimed_total, per_page=per_page)
        for n, chunk in enumerate(chunks, start=1)
    }


def _customer_records():
    return [
        synthetic_customer_record(id=i, customer_number=f"K-{i}", url_show=f"/customer/{i}")
        for i in CUSTOMER_IDS
    ]


def _project_records():
    return [
        synthetic_project_record(id=i, project_number=f"P-{i}", url_show=f"/project/{i}")
        for i in range(201, 205)
    ]


def _supplier_records():
    return [
        synthetic_supplier_record(id=i, url_show=f"/supplier/{i}")
        for i in range(301, 306)
    ]


def _customer_details():
    return {
        101: customer_detail_html_with_mailto(email=CUSTOMER_EMAILS[101]),
        102: customer_detail_html_with_mailto(email=CUSTOMER_EMAILS[102]),
        103: customer_detail_html_without_mailto(),
    }


class FullRoundSession:
    """Routeert verzoeken op pad, query en methode voor de hele ronde (login,
    sessieprobe, drie pagineerendpoints, customer-detailpagina's), zonder
    netwerk. Houdt net als een echte requests.Session een cookiejar bij."""

    def __init__(
        self,
        *,
        customer_pages,
        project_pages,
        supplier_pages,
        customer_details,
        probe_live,
        login_succeeds=True,
        cookies=None,
    ):
        self.calls = []
        self.cookies = dict(cookies) if cookies else {}
        self._customer_pages = customer_pages
        self._project_pages = project_pages
        self._supplier_pages = supplier_pages
        self._customer_details = customer_details
        self._probe_live = probe_live
        self._login_succeeds = login_succeeds
        self._logged_in = False

    def request(self, method, url, params=None, data=None):
        self.calls.append({"method": method, "url": url, "params": params, "data": data})
        query = dict(params or {})
        response = self._dispatch(method, url, query)
        self.cookies.update(response.set_cookies)
        return response

    def _probe_response(self):
        if self._probe_live or self._logged_in:
            return StubResponse(200, body='{"ok": true}', headers={"Content-Type": "application/json"})
        return StubResponse(302, headers={"Location": "/login"})

    def _dispatch(self, method, url, query):
        if method == "GET" and url == "/login":
            return StubResponse(200, body=LOGIN_PAGE_HTML)
        if method == "POST" and url == "/login":
            if self._login_succeeds:
                self._logged_in = True
                return StubResponse(302, headers={"Location": "/dashboard"})
            return StubResponse(302, headers={"Location": "/login"})
        if method == "GET" and url == PROBE_PATH:
            return self._probe_response()
        if method == "GET" and url == "/customer/all.json":
            page = int(query.get("page", 1))
            return StubResponse(200, body=json.dumps(self._customer_pages[page]))
        if method == "GET" and url == "/project/all.json":
            page = int(query.get("page", 1))
            return StubResponse(200, body=json.dumps(self._project_pages[page]))
        if method == "GET" and url == "/supplier.json":
            page = int(query.get("page", 1))
            return StubResponse(200, body=json.dumps(self._supplier_pages[page]))
        if method == "GET" and url.startswith("/customer/") and url[len("/customer/") :].isdigit():
            customer_id = int(url[len("/customer/") :])
            return StubResponse(200, body=self._customer_details[customer_id])
        raise AssertionError(f"onverwacht verzoek: {method} {url} {query}")


def _make_client(session):
    from simpul_extract.http_client import SimpulHTTPClient

    return SimpulHTTPClient(session, delay=0.0, sleep=lambda seconds: None)


def _run_round(session, *, run_id="run-full", store=None, stdout=None):
    client = _make_client(session)
    pot = StubCookiePot(initial=INITIAL_COOKIES)
    store = store if store is not None else InMemoryUpsertStore(now=_CountingClock())
    stdout = stdout if stdout is not None else io.StringIO()

    exit_code = run(
        client=client,
        session=session,
        pot=pot,
        store=store,
        username="gebruiker",
        password=GEHEIM_WACHTWOORD,
        stdout=stdout,
        run_id=run_id,
        now=_CountingClock(),
        probe_path=PROBE_PATH,
    )
    return exit_code, store, pot, stdout


class TestVolledigeRondeOpStubs(unittest.TestCase):
    def _session(self):
        return FullRoundSession(
            customer_pages=_fractal_pages(_customer_records(), per_page=2),
            project_pages=_fractal_pages(_project_records(), per_page=2),
            supplier_pages=_laravel_pages(_supplier_records(), per_page=3),
            customer_details=_customer_details(),
            probe_live=True,
            cookies=INITIAL_COOKIES,
        )

    def test_ronde_eindigt_met_exit_0(self):
        exit_code, _, _, _ = _run_round(self._session())
        self.assertEqual(exit_code, EXIT_OK)

    def test_drie_kloppende_tellingen(self):
        _, store, _, _ = _run_round(self._session())

        self.assertEqual(len(store.tables["customer"]), 3)
        self.assertEqual(len(store.tables["project"]), 4)
        self.assertEqual(len(store.tables["supplier"]), 5)

    def test_emails_worden_gevuld_vanuit_de_detailpagina(self):
        _, store, _, _ = _run_round(self._session())

        rows = store.tables["customer"]
        self.assertEqual(rows[101]["email"], CUSTOMER_EMAILS[101])
        self.assertEqual(rows[102]["email"], CUSTOMER_EMAILS[102])
        self.assertIsNone(rows[103]["email"], "geen mailto-anker moet None opleveren, geen fout")

    def test_auditregels_per_entiteit_zijn_compleet(self):
        _, store, _, _ = _run_round(self._session(), run_id="run-audit")

        rows = store.tables[EXTRACTION_RUN_TABLE]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["run_id"] == "run-audit" for row in rows.values()))
        self.assertTrue(all(row["note"] is None for row in rows.values()))
        self.assertTrue(
            all("complete" not in row for row in rows.values()),
            "complete is een generated column en mag niet aangeleverd worden",
        )
        self.assertEqual(
            {row["entity"] for row in rows.values()}, {"customer", "project", "supplier"}
        )

    def test_geroteerde_cookie_wordt_teruggeschreven(self):
        _, _, pot, _ = _run_round(self._session())

        self.assertEqual(len(pot.write_calls), 1)

    def test_slotoverzicht_noemt_gevonden_gemeld_en_emailaantal(self):
        _, _, _, stdout = _run_round(self._session())

        overzicht = stdout.getvalue()
        self.assertIn("customer: gevonden 3, gemeld 3", overzicht)
        self.assertIn("project: gevonden 4, gemeld 4", overzicht)
        self.assertIn("supplier: gevonden 5, gemeld 5", overzicht)
        self.assertIn("e-mailadressen gevonden: 2", overzicht)
        self.assertIn("ok", overzicht)

    def test_geen_cookiewaarde_of_wachtwoord_in_het_slotoverzicht(self):
        _, _, _, stdout = _run_round(self._session())

        overzicht = stdout.getvalue()
        self.assertNotIn(GEHEIM_WACHTWOORD, overzicht)
        for naam, waarde in INITIAL_COOKIES.items():
            self.assertNotIn(waarde, overzicht)


class TestSessieDoodEnLoginMislukt(unittest.TestCase):
    def _session(self):
        return FullRoundSession(
            customer_pages=_fractal_pages(_customer_records(), per_page=2),
            project_pages=_fractal_pages(_project_records(), per_page=2),
            supplier_pages=_laravel_pages(_supplier_records(), per_page=3),
            customer_details=_customer_details(),
            probe_live=False,
            login_succeeds=False,
            cookies=INITIAL_COOKIES,
        )

    def test_exit_code_is_2(self):
        exit_code, _, _, _ = _run_round(self._session())
        self.assertEqual(exit_code, EXIT_SESSION_LOST)

    def test_geen_enkele_upsert_naar_de_schrijflaag(self):
        _, store, _, _ = _run_round(self._session())
        self.assertEqual(store.tables, {}, "een mislukte login mag geen enkele upsert opleveren")

    def test_cookiepot_wordt_niet_aangeraakt(self):
        _, _, pot, _ = _run_round(self._session())
        self.assertEqual(pot.write_calls, [])

    def test_geen_cookiewaarde_of_wachtwoord_op_stdout(self):
        _, _, _, stdout = _run_round(self._session())

        overzicht = stdout.getvalue()
        self.assertNotIn(GEHEIM_WACHTWOORD, overzicht)
        for naam, waarde in INITIAL_COOKIES.items():
            self.assertNotIn(waarde, overzicht)


class TestEntiteitOnvolledig(unittest.TestCase):
    def _session(self):
        # supplier: de bron meldt total=5, maar levert feitelijk maar 4 rijen.
        supplier_pages = _laravel_pages(_supplier_records()[:4], per_page=3, total=5)
        return FullRoundSession(
            customer_pages=_fractal_pages(_customer_records(), per_page=2),
            project_pages=_fractal_pages(_project_records(), per_page=2),
            supplier_pages=supplier_pages,
            customer_details=_customer_details(),
            probe_live=True,
            cookies=INITIAL_COOKIES,
        )

    def test_exit_code_is_niet_nul_en_niet_2(self):
        exit_code, _, _, _ = _run_round(self._session())
        self.assertNotEqual(exit_code, EXIT_OK)
        self.assertNotEqual(exit_code, EXIT_SESSION_LOST)
        self.assertEqual(exit_code, EXIT_INCOMPLETE)

    def test_geroteerde_cookie_wordt_toch_teruggeschreven(self):
        _, _, pot, _ = _run_round(self._session())
        self.assertEqual(
            len(pot.write_calls), 1,
            "een datafout mag de sessie niet kosten: de cookie moet alsnog terug naar de pot",
        )

    def test_auditregel_van_de_falende_entiteit_verklaart_zich_in_note(self):
        _, store, _, _ = _run_round(self._session(), run_id="run-onvolledig")

        rows = store.tables[EXTRACTION_RUN_TABLE]
        by_entity = {row["entity"]: row for row in rows.values()}
        self.assertEqual(by_entity["supplier"]["note"], "4 weggeschreven, bron meldt 5")
        self.assertEqual(by_entity["supplier"]["rows_stored"], 4)
        self.assertEqual(by_entity["supplier"]["source_total"], 5)
        self.assertIsNone(by_entity["customer"]["note"])
        self.assertIsNone(by_entity["project"]["note"])
        # De database leidt `complete` af uit rows_stored en source_total; de
        # aantallen hierboven zijn dus tegelijk de pin op wat daar komt te staan.
        self.assertTrue(all("complete" not in row for row in rows.values()))

    def test_andere_entiteiten_zijn_nog_steeds_correct_weggeschreven(self):
        _, store, _, _ = _run_round(self._session())

        self.assertEqual(len(store.tables["customer"]), 3)
        self.assertEqual(len(store.tables["project"]), 4)
        self.assertEqual(len(store.tables["supplier"]), 4)


if __name__ == "__main__":
    unittest.main()


class TestBronLevertEenProjectDubbel(unittest.TestCase):
    """Gemeten op 2026-08-29 tegen de echte bron: `/project/all.json` meldt
    2793, levert 2793 rijen en bevat 2778 unieke id's — 15 projecten komen
    twee keer langs, veld voor veld identiek. Ronde 5 viel daarop met
    Postgres-21000 ("ON CONFLICT DO UPDATE command cannot affect row a second
    time").

    Deze stubset bootst dat na in het klein: vier projecten, waarvan er één
    twee keer wordt uitgedeeld, en een bron die 5 als totaal meldt.
    """

    DUBBEL_ID = 202

    def _session(self):
        records = _project_records()
        dubbel = next(r for r in records if r["id"] == self.DUBBEL_ID)
        met_dubbel = records + [dict(dubbel)]
        return FullRoundSession(
            customer_pages=_fractal_pages(_customer_records(), per_page=2),
            project_pages=_fractal_pages(met_dubbel, per_page=2, total=len(met_dubbel)),
            supplier_pages=_laravel_pages(_supplier_records(), per_page=3),
            customer_details=_customer_details(),
            probe_live=True,
            cookies=INITIAL_COOKIES,
        )

    def test_ronde_eindigt_met_exit_0(self):
        """Zonder ontdubbelen weigert de schrijflaag de batch zoals Postgres
        dat doet, en haalt de ronde exit 0 niet."""
        exit_code, _, _, _ = _run_round(self._session())
        self.assertEqual(exit_code, EXIT_OK)

    def test_de_dubbele_rij_wordt_maar_een_keer_aangeboden(self):
        _, store, _, _ = _run_round(self._session())

        self.assertEqual(len(store.tables["project"]), 4)
        self.assertIn(self.DUBBEL_ID, store.tables["project"])

    def test_gemeld_totaal_wordt_gecorrigeerd_zodat_de_ronde_volledig_is(self):
        """De bron telt haar dubbel geleverde rij mee in het totaal. `complete`
        is een generated column over `rows_stored = source_total`, dus zonder
        correctie meldt elke ronde ONVOLLEDIG op een eigenaardigheid van de
        bron. Zie adr/2026-08-29-volledig-telt-entiteiten.md."""
        _, store, _, _ = _run_round(self._session(), run_id="run-dubbel")

        regel = next(
            row
            for row in store.tables[EXTRACTION_RUN_TABLE].values()
            if row["entity"] == "project"
        )
        self.assertEqual(regel["rows_stored"], 4)
        self.assertEqual(regel["source_total"], 4)
        self.assertNotIn(
            "complete", regel, "complete is generated en mag niet aangeleverd worden"
        )

    def test_de_note_bewaart_wat_de_bron_werkelijk_meldde(self):
        """Het rauwe getal mag niet verdwijnen doordat de som klopt: een bron
        die morgen 300 rijen dubbel levert moet zichtbaar blijven."""
        _, store, _, _ = _run_round(self._session(), run_id="run-note")

        regel = next(
            row
            for row in store.tables[EXTRACTION_RUN_TABLE].values()
            if row["entity"] == "project"
        )
        self.assertEqual(regel["note"], "bron meldde 5, 1 rijen dubbel geleverd")

    def test_entiteiten_zonder_dubbelen_houden_een_lege_note(self):
        _, store, _, _ = _run_round(self._session(), run_id="run-schoon")

        noten = {
            row["entity"]: row["note"]
            for row in store.tables[EXTRACTION_RUN_TABLE].values()
        }
        self.assertIsNone(noten["customer"])
        self.assertIsNone(noten["supplier"])

    def test_slotoverzicht_noemt_het_dubbel_geleverde_aantal(self):
        _, _, _, stdout = _run_round(self._session())

        overzicht = stdout.getvalue()
        self.assertIn("project: gevonden 4, gemeld 4, 1 dubbel geleverd (ok)", overzicht)
        self.assertIn("customer: gevonden 3, gemeld 3 (ok)", overzicht)
