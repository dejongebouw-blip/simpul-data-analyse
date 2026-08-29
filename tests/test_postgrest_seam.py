"""Toetst de naad tussen de code en het échte PostgREST-eindpunt: de basis-url
die uit `SUPABASE_URL` volgt, en de kolomnamen waarmee de cookiepot
`simpul_raw.session_cookie` aanspreekt.

Deze naad viel eerder buiten elke test. Q5, Q6 en Q14 stubben de pot, en
`tests/test_persistence.py` gaf `SUPABASE_URL` een waarde die het pad
`/rest/v1` al droeg — een vorm die Supabase zelf nooit uitgeeft. Daardoor
bleven twee fouten staan tot H4 uit het testplan ze op de draaiende omgeving
raakte:

1. `postgrest_store_from_env` plakte de tabelnaam direct achter de
   projecturl, dus `https://<ref>.supabase.co/customer` in plaats van
   `https://<ref>.supabase.co/rest/v1/customer`.
2. `PostgrestCookiePot` schreef één rij `id=1` met een JSON-kolom `cookies`,
   terwijl de tabel `name` (primaire sleutel), `value` en `updated_at` draagt.

De verwachte kolomnamen hieronder zijn met de hand overgetypt uit issue 11,
onafhankelijk van `db/schema-postgres.sql` en van de code — dezelfde aanpak
als `tests/test_field_contract.py` en `tests/test_schema_sql.py`.

Netwerkloos: elke sessie is een stub, er verlaat geen verzoek dit proces.
"""
from __future__ import annotations

import unittest

from simpul_extract.__main__ import (
    DEFAULT_SIMPUL_BASE_URL,
    PostgrestCookiePot,
    simpul_base_url,
)
from simpul_extract.storage import (
    PostgrestUpsertStore,
    postgrest_base_url,
    postgrest_store_from_env,
)

# Letterlijk uit issue 11: `name` text primary key; `value` text; `updated_at`.
SESSION_COOKIE_COLUMNS = ("name", "value", "updated_at")
SESSION_COOKIE_KEY = "name"

# De vorm die Supabase zelf uitgeeft voor `SUPABASE_URL`: geen pad.
PROJECT_URL = "https://ildofjfbqusjoaanwbmi.supabase.co"


class _StubResponse:
    def __init__(self, payload=None):
        self._payload = payload if payload is not None else []
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _RecordingSession:
    """Registreert elk verzoek en verlaat het proces nooit."""

    def __init__(self, get_payload=None):
        self.calls = []
        self._get_payload = get_payload

    def get(self, url, params=None, headers=None):
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers})
        return _StubResponse(self._get_payload)

    def post(self, url, json=None, params=None, headers=None):
        self.calls.append(
            {"method": "POST", "url": url, "json": json, "params": params, "headers": headers}
        )
        return _StubResponse()


class TestPostgrestBaseUrl(unittest.TestCase):
    def test_projecturl_krijgt_rest_v1(self) -> None:
        self.assertEqual(postgrest_base_url(PROJECT_URL), PROJECT_URL + "/rest/v1")

    def test_afsluitende_slash_geeft_geen_dubbel_pad(self) -> None:
        self.assertEqual(postgrest_base_url(PROJECT_URL + "/"), PROJECT_URL + "/rest/v1")

    def test_expliciet_eindpunt_blijft_ongemoeid(self) -> None:
        expliciet = PROJECT_URL + "/rest/v1"
        self.assertEqual(postgrest_base_url(expliciet), expliciet)

    def test_store_uit_env_schrijft_naar_rest_v1(self) -> None:
        """Tegen-pin op de echte fout: met de projecturl uit `.env` moet de
        upsert op `/rest/v1/customer` landen, niet op de projectroot."""
        store = postgrest_store_from_env(
            {"SUPABASE_URL": PROJECT_URL, "SUPABASE_SECRET_KEY": "geheim"}
        )
        session = _RecordingSession()
        store._session = session  # netwerkloos maken zonder de constructor te wijzigen

        store.upsert("customer", [{"id": 1, "title": "Klant"}])

        self.assertEqual(session.calls[0]["url"], PROJECT_URL + "/rest/v1/customer")

    def test_zonder_rest_v1_zou_de_url_fout_zijn(self) -> None:
        """Bewijst dat de bovenstaande assertie werkelijk onderscheidt: de
        oude, kale samenstelling geeft een andere url."""
        oud = PROJECT_URL.rstrip("/") + "/customer"
        self.assertNotEqual(oud, PROJECT_URL + "/rest/v1/customer")


class TestSimpulBaseUrl(unittest.TestCase):
    """De PRD-`docker run` geeft alleen de vier geheimen mee, dus de bron-url
    moet uit de code komen. Stond hij op een lege string, dan werd elk pad
    relatief en faalde de ronde vóór het eerste verzoek."""

    # Letterlijk uit prd.md en project.md.
    BRON = "https://schoutenhoveniers.simpul.nl"

    def test_default_is_de_bron_uit_het_prd(self) -> None:
        self.assertEqual(DEFAULT_SIMPUL_BASE_URL, self.BRON)

    def test_lege_env_valt_terug_op_de_bron(self) -> None:
        self.assertEqual(simpul_base_url({}), self.BRON)
        self.assertEqual(simpul_base_url({"SIMPUL_BASE_URL": ""}), self.BRON)

    def test_expliciete_overschrijving_wint(self) -> None:
        self.assertEqual(
            simpul_base_url({"SIMPUL_BASE_URL": "https://acceptatie.invalid/"}),
            "https://acceptatie.invalid",
        )

    def test_lege_basis_zou_een_relatief_pad_geven(self) -> None:
        """Tegen-pin: bewijst dat de oude terugval werkelijk kapot was."""
        self.assertNotEqual("" + "/customer/list", self.BRON + "/customer/list")


class TestCookiePotKolomcontract(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _RecordingSession(
            get_payload=[
                {"name": "simpul_session", "value": "abc"},
                {"name": "XSRF-TOKEN", "value": "def"},
            ]
        )
        self.pot = PostgrestCookiePot(PROJECT_URL, "geheim", session=self.session)

    def test_leest_naam_en_waarde_uit_de_tabel(self) -> None:
        cookies = self.pot.read()

        call = self.session.calls[0]
        self.assertEqual(call["url"], PROJECT_URL + "/rest/v1/session_cookie")
        gevraagd = tuple(call["params"]["select"].split(","))
        self.assertEqual(gevraagd, ("name", "value"))
        for kolom in gevraagd:
            self.assertIn(kolom, SESSION_COOKIE_COLUMNS)
        self.assertEqual(cookies, {"simpul_session": "abc", "XSRF-TOKEN": "def"})

    def test_lege_tabel_geeft_lege_pot(self) -> None:
        session = _RecordingSession(get_payload=[])
        pot = PostgrestCookiePot(PROJECT_URL, "geheim", session=session)
        self.assertEqual(pot.read(), {})

    def test_schrijft_een_rij_per_cookie_met_de_echte_kolommen(self) -> None:
        self.pot.write({"simpul_session": "abc", "XSRF-TOKEN": "def"})

        call = self.session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], PROJECT_URL + "/rest/v1/session_cookie")
        self.assertEqual(call["params"], {"on_conflict": SESSION_COOKIE_KEY})
        self.assertEqual(len(call["json"]), 2)
        for rij in call["json"]:
            self.assertEqual(tuple(rij), SESSION_COOKIE_COLUMNS)
        self.assertEqual(
            {rij["name"]: rij["value"] for rij in call["json"]},
            {"simpul_session": "abc", "XSRF-TOKEN": "def"},
        )

    def test_schrijft_geen_kolom_die_niet_bestaat(self) -> None:
        """Tegen-pin op de echte fout: `id` en `cookies` bestaan niet in
        `simpul_raw.session_cookie` en mogen dus nergens in de payload staan."""
        self.pot.write({"simpul_session": "abc"})

        rij = self.session.calls[0]["json"][0]
        for verboden in ("id", "cookies"):
            self.assertNotIn(verboden, rij)
        self.assertNotIn("id", self.session.calls[0]["params"].values())

    def test_lege_pot_schrijven_doet_geen_verzoek(self) -> None:
        self.pot.write({})
        self.assertEqual(self.session.calls, [])

    def test_schema_header_staat_op_simpul_raw(self) -> None:
        self.pot.write({"simpul_session": "abc"})
        headers = self.session.calls[0]["headers"]
        self.assertEqual(headers["Content-Profile"], "simpul_raw")
        self.assertIn("merge-duplicates", headers["Prefer"])


if __name__ == "__main__":
    unittest.main()


# Letterlijk uit issue 11: `id` bigint **generated always as identity** primary
# key; `run_id` uuid; `started_at`, `finished_at` timestamptz; `entity` text;
# `rows_stored`, `source_total` integer; `complete` boolean; `note` text.
# Geen `fetched_at` — issue 11 zondert extraction_run en session_cookie daar
# expliciet van uit.
EXTRACTION_RUN_COLUMNS = (
    "run_id", "started_at", "finished_at", "entity",
    "rows_stored", "source_total", "complete", "note",
)


class TestAuditregelNaarDeEchteTabel(unittest.TestCase):
    """De ronde van 2026-08-29 haalde 951 relaties binnen en viel toen om met
    HTTP 400 op `extraction_run`: `upsert()` stuurde een tekst-`id` naar een
    identity-kolom én een `fetched_at` die de tabel niet heeft. Beide fouten
    waren onzichtbaar omdat elke toets tot dan een in-memory store gebruikte."""

    def _append_call(self):
        session = _RecordingSession()
        store = PostgrestUpsertStore(
            postgrest_base_url(PROJECT_URL), "sleutel", session=session, now=lambda: "2026-08-29T00:00:00+00:00"
        )
        store.append("extraction_run", [{
            "run_id": "0123456789abcdef0123456789abcdef",
            "started_at": "2026-08-29T21:20:00+00:00",
            "finished_at": "2026-08-29T21:29:00+00:00",
            "entity": "customer",
            "rows_stored": 951,
            "source_total": 951,
            "complete": True,
            "note": None,
        }])
        (call,) = [c for c in session.calls if c["method"] == "POST"]
        return call

    def test_stuurt_geen_id_want_de_kolom_is_generated_always(self):
        (rij,) = self._append_call()["json"]
        self.assertNotIn("id", rij)

    def test_plakt_geen_fetched_at_op_een_tabel_die_die_kolom_niet_heeft(self):
        (rij,) = self._append_call()["json"]
        self.assertNotIn("fetched_at", rij)

    def test_stuurt_precies_de_kolommen_uit_issue_11(self):
        (rij,) = self._append_call()["json"]
        self.assertEqual(set(rij), set(EXTRACTION_RUN_COLUMNS))

    def test_gebruikt_geen_on_conflict_want_een_auditregel_is_een_toevoeging(self):
        call = self._append_call()
        self.assertFalse(call.get("params"))
        self.assertNotIn("merge-duplicates", call["headers"].get("Prefer", ""))

    def test_schrijft_naar_het_schema_simpul_raw_op_het_rest_pad(self):
        call = self._append_call()
        self.assertEqual(call["url"], f"{PROJECT_URL}/rest/v1/extraction_run")
        self.assertEqual(call["headers"]["Content-Profile"], "simpul_raw")


class TestWriteExtractionRunTegenDeEchteSchrijflaag(unittest.TestCase):
    """Pint de sámenstelling, niet de losse laag: wat `write_extraction_run`
    werkelijk over de lijn zet als de store de echte PostgREST-store is.

    De naadtoetsen hierboven roepen `append()` rechtstreeks aan en zouden dus
    groen blijven als `write_extraction_run` weer `upsert()` ging gebruiken —
    precies de fout van 2026-08-29. Deze toets sluit die weg af.
    """

    def _post(self):
        from simpul_extract.completeness import write_extraction_run

        session = _RecordingSession()
        store = PostgrestUpsertStore(
            postgrest_base_url(PROJECT_URL), "sleutel", session=session,
            now=lambda: "2026-08-29T00:00:00+00:00",
        )
        write_extraction_run(
            store,
            run_id="0123456789abcdef0123456789abcdef",
            started_at="2026-08-29T21:20:00+00:00",
            finished_at="2026-08-29T21:29:00+00:00",
            entity="customer",
            rows_stored=951,
            source_total=951,
            complete=True,
            note=None,
        )
        (call,) = [c for c in session.calls if c["method"] == "POST"]
        return call

    def test_de_auditregel_draagt_geen_id(self):
        (rij,) = self._post()["json"]
        self.assertNotIn("id", rij)

    def test_de_auditregel_draagt_geen_fetched_at(self):
        (rij,) = self._post()["json"]
        self.assertNotIn("fetched_at", rij)

    def test_de_auditregel_draagt_precies_de_kolommen_uit_issue_11(self):
        (rij,) = self._post()["json"]
        self.assertEqual(set(rij), set(EXTRACTION_RUN_COLUMNS))

    def test_er_gaat_geen_on_conflict_mee(self):
        call = self._post()
        self.assertFalse(call.get("params"))
        self.assertNotIn("merge-duplicates", call["headers"].get("Prefer", ""))
