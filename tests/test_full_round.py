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


def _volledige_sessie():
    return FullRoundSession(
        customer_pages=_fractal_pages(_customer_records(), per_page=2),
        project_pages=_fractal_pages(_project_records(), per_page=2),
        supplier_pages=_laravel_pages(_supplier_records(), per_page=3),
        customer_details=_customer_details(),
        probe_live=True,
        cookies=INITIAL_COOKIES,
    )


class TestVerwijderdetectieOverMeerdereRondes(unittest.TestCase):
    """Toetst de markeerstap (issue 03) op de naad van `run()`: twee of drie
    opeenvolgende aanroepen op dezelfde store, zoals scenario's 1, 3, 4 en 5
    uit de Testing Decisions van het PRD beschrijven."""

    def test_verdwenen_rij_wordt_gemarkeerd_maar_blijft_bestaan(self):
        """Scenario 1: een rij die niet meer geleverd wordt in een verder
        volledige ronde krijgt `missing_since`, de rij zelf blijft staan, en
        de markeerstap meldt `marked=1`."""
        store = InMemoryUpsertStore(now=_CountingClock())
        _run_round(_volledige_sessie(), run_id="run-1", store=store)
        self.assertIsNone(store.tables["customer"][103].get("missing_since"))

        zonder_103 = [r for r in _customer_records() if r["id"] != 103]
        sessie = FullRoundSession(
            customer_pages=_fractal_pages(zonder_103, per_page=2),
            project_pages=_fractal_pages(_project_records(), per_page=2),
            supplier_pages=_laravel_pages(_supplier_records(), per_page=3),
            customer_details={101: _customer_details()[101], 102: _customer_details()[102]},
            probe_live=True,
            cookies=INITIAL_COOKIES,
        )

        exit_code, store, _, stdout = _run_round(sessie, run_id="run-2", store=store)

        self.assertEqual(exit_code, EXIT_OK)
        self.assertIn(103, store.tables["customer"], "mark_missing mag nooit een rij verwijderen")
        self.assertIsNotNone(store.tables["customer"][103]["missing_since"])
        self.assertIn(
            "customer: gevonden 2, gemeld 2 (ok), gemarkeerd 1, teruggekeerd 0",
            stdout.getvalue(),
        )

    def test_teruggekeerde_rij_verliest_missing_since_in_dezelfde_ronde(self):
        """Scenario 3: een rij die eerst gemarkeerd is en deze ronde weer
        `last_seen_run` van de huidige ronde draagt, verliest haar
        `missing_since` meteen, en de markeerstap meldt `returned=1`.

        `InMemoryUpsertStore.upsert()` vervangt een rij in zijn geheel zodra
        er iets verschilt (issue 02), in plaats van kolomsgewijs te mergen
        zoals een echte partial-column SQL-upsert dat doet -- een rij die
        opnieuw in de HTTP-levering zit zou `missing_since` daardoor al vóór
        de markeerstap kwijtraken, terwijl Postgres een kolom die niet in de
        payload zit (`missing_since`) nooit aanraakt. Om puur de bedrading in
        `run()` te toetsen -- niet die nepstore-fidelity, die buiten de
        scope van issue 03 valt -- laat deze toets rij 103 buiten de
        HTTP-levering van ronde 3 en zet vooraf de `last_seen_run` die een
        échte upsert die ronde zou hebben gezet, zoals
        `tests/test_persistence.py::test_teruggekeerde_rij_verliest_missing_since`
        dat ook rechtstreeks op de store doet."""
        store = InMemoryUpsertStore(now=_CountingClock())
        _run_round(_volledige_sessie(), run_id="run-1", store=store)
        zonder_103 = [r for r in _customer_records() if r["id"] != 103]
        tussensessie = FullRoundSession(
            customer_pages=_fractal_pages(zonder_103, per_page=2),
            project_pages=_fractal_pages(_project_records(), per_page=2),
            supplier_pages=_laravel_pages(_supplier_records(), per_page=3),
            customer_details={101: _customer_details()[101], 102: _customer_details()[102]},
            probe_live=True,
            cookies=INITIAL_COOKIES,
        )
        _run_round(tussensessie, run_id="run-2", store=store)
        self.assertIsNotNone(store.tables["customer"][103]["missing_since"])

        store.tables["customer"][103]["last_seen_run"] = "run-3"
        exit_code, store, _, stdout = _run_round(tussensessie, run_id="run-3", store=store)

        self.assertEqual(exit_code, EXIT_OK)
        self.assertIsNone(store.tables["customer"][103]["missing_since"])
        self.assertIn(
            "customer: gevonden 2, gemeld 2 (ok), gemarkeerd 0, teruggekeerd 1",
            stdout.getvalue(),
        )

    def test_twee_identieke_volledige_rondes_markeren_en_laten_niets_terugkeren(self):
        """Scenario 4: idempotentie geldt ook voor de markeerstap zelf -- een
        tweede, ongewijzigde ronde markeert nul rijen en laat nul rijen
        terugkeren, en de bestaande tellingtoetsen (aantal rijen per tabel)
        blijven kloppen."""
        store = InMemoryUpsertStore(now=_CountingClock())
        _run_round(_volledige_sessie(), run_id="run-1", store=store)

        exit_code, store, _, stdout = _run_round(_volledige_sessie(), run_id="run-2", store=store)

        self.assertEqual(exit_code, EXIT_OK)
        self.assertEqual(len(store.tables["customer"]), 3)
        self.assertEqual(len(store.tables["project"]), 4)
        self.assertEqual(len(store.tables["supplier"]), 5)
        overzicht = stdout.getvalue()
        for entity, gevonden in (("customer", 3), ("project", 4), ("supplier", 5)):
            self.assertIn(
                f"{entity}: gevonden {gevonden}, gemeld {gevonden} (ok), gemarkeerd 0, teruggekeerd 0",
                overzicht,
            )

    def test_voor_migratie_rij_zonder_last_seen_run_wordt_bij_afwezigheid_gemarkeerd(self):
        """Scenario 5: een rij van vóór de migratie (`last_seen_run is null`)
        telt niet als gezien in een volledige ronde die haar niet levert, en
        wordt dus gemarkeerd -- de nul-veilige semantiek uit issue 02."""
        store = InMemoryUpsertStore(now=_CountingClock())
        store.tables.setdefault("customer", {})[999] = {
            "id": 999,
            "title": "Spookrelatie van vóór de migratie",
            "fetched_at": "2026-01-01T00:00:00+00:00",
        }

        exit_code, store, _, _ = _run_round(_volledige_sessie(), run_id="run-1", store=store)

        self.assertEqual(exit_code, EXIT_OK)
        self.assertIn(999, store.tables["customer"])
        self.assertIsNotNone(store.tables["customer"][999]["missing_since"])


def _onvolledige_customer_sessie():
    # De bron meldt nog altijd total=3 (ongewijzigd), maar levert er deze
    # ronde nog maar 2: een hapering, geen bewuste verdwijning van rij 103.
    onvolledige_customer_pages = _fractal_pages(
        [r for r in _customer_records() if r["id"] != 103], per_page=2, total=3,
    )
    return FullRoundSession(
        customer_pages=onvolledige_customer_pages,
        project_pages=_fractal_pages(_project_records(), per_page=2),
        supplier_pages=_laravel_pages(_supplier_records(), per_page=3),
        customer_details={101: _customer_details()[101], 102: _customer_details()[102]},
        probe_live=True,
        cookies=INITIAL_COOKIES,
    )


class TestOnvolledigeEntiteitSlaatMarkerenOver(unittest.TestCase):
    """Scenario 2 uit de Testing Decisions: een entiteit die deze ronde
    onvolledig is markeert niets voor die entiteit en wijzigt geen bestaande
    `missing_since`. Ronde 1 legt customer 103 vast met `last_seen_run=run-1`
    en een volledige ronde; ronde 2 levert customer 103 niet meer aan, maar
    de bron meldt nog altijd total=3 -- rows_stored=2 != source_total=3, dus
    onvolledig voor customer. Rij 103 heeft na ronde 2 dus nog steeds
    `last_seen_run=run-1`: precies de toestand waarop `store.mark_missing`
    zou bijten als de volledigheidsguard in `run()` ontbrak (zie de
    tegen-pin hieronder)."""

    def test_onvolledige_ronde_markeert_niets_en_laat_missing_since_ongemoeid(self):
        store = InMemoryUpsertStore(now=_CountingClock())
        _run_round(_volledige_sessie(), run_id="run-1", store=store)
        self.assertIsNone(store.tables["customer"][103].get("missing_since"))

        exit_code, store, _, stdout = _run_round(
            _onvolledige_customer_sessie(), run_id="run-2", store=store,
        )

        self.assertEqual(exit_code, EXIT_INCOMPLETE)
        self.assertIsNone(
            store.tables["customer"][103].get("missing_since"),
            "een onvolledige ronde mag nooit markeren, ook niet voor een rij "
            "die deze ronde niet geleverd is",
        )
        overzicht = stdout.getvalue()
        self.assertIn(
            "customer: gevonden 2, gemeld 3 (onvolledig), markeerstap overgeslagen (onvolledig)",
            overzicht,
        )

    def test_andere_entiteiten_markeren_gewoon_door_ondanks_de_onvolledige_customer(self):
        store = InMemoryUpsertStore(now=_CountingClock())
        _run_round(_volledige_sessie(), run_id="run-1", store=store)

        _, store, _, stdout = _run_round(_onvolledige_customer_sessie(), run_id="run-2", store=store)

        overzicht = stdout.getvalue()
        self.assertIn("project: gevonden 4, gemeld 4 (ok), gemarkeerd 0, teruggekeerd 0", overzicht)
        self.assertIn("supplier: gevonden 5, gemeld 5 (ok), gemarkeerd 0, teruggekeerd 0", overzicht)


class TestTegenPinMarkeerstapZonderGuardZouBijten(unittest.TestCase):
    """Tegen-pin voor scenario 2 (lesson
    2026-08-29-een-tegen-pin-die-niet-bijt-is-geen-tegen-pin.md): bewijst dat
    de assertie in `TestOnvolledigeEntiteitSlaatMarkerenOver` niet toevallig
    groen is.

    Na dezelfde twee rondes als hierboven roept deze test `store.mark_missing`
    rechtstreeks aan -- precies wat `run()` zou doen als de `if complete:`-
    guard ontbrak, want de markeerstap raakt alleen de entiteitstabel, niet
    `extraction_run` of de cookiepot, dus die kale aanroep is exact de
    kapotte variant. Die kapotte variant markeert rij 103 wél. Zodra de guard
    uit `run()` verdwijnt, gaat de assertie in
    `TestOnvolledigeEntiteitSlaatMarkerenOver` dus aantoonbaar rood -- de
    tegen-pin bijt."""

    def test_markeerstap_zonder_guard_zou_de_gehaperde_rij_ten_onrechte_markeren(self):
        store = InMemoryUpsertStore(now=_CountingClock())
        _run_round(_volledige_sessie(), run_id="run-1", store=store)
        _run_round(_onvolledige_customer_sessie(), run_id="run-2", store=store)
        self.assertIsNone(store.tables["customer"][103].get("missing_since"))

        marked_without_guard, _ = store.mark_missing("customer", "run-2")

        self.assertEqual(
            marked_without_guard, 1,
            "zonder de volledigheidsguard zou de markeerstap rij 103 ten "
            "onrechte als verdwenen markeren op basis van een bronhapering "
            "-- dat is precies wat de guard in run() moet voorkomen",
        )
