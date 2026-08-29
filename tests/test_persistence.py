"""Toetst de schrijflaag (`simpul_extract/storage.py`): idempotente upsert
via de injecteerbare interface `upsert(table, rows)`, zonder Postgres en
zonder netwerk.

Reproductie eerst: schrijf dezelfde rij twee keer met een gewijzigd veld via
de nepimplementatie. Zolang `upsert()` een kale insert is, staan er na afloop
twee rijen voor dezelfde id — dat maakt
`test_tweemaal_dezelfde_rij_levert_een_rij_op_met_bijgewerkte_waarde` rood.
Groen betekent: één rij, met de bijgewerkte waarde.

Geen enkele test in dit bestand doet een verzoek naar het echte
Supabase-project: de PostgREST-implementatie wordt hier uitsluitend tegen een
in-process stub-sessie getoetst (zoals `tests/_stubs.py` dat voor de
HTTP-laag doet), nooit tegen een draaiende omgeving.
"""
from __future__ import annotations

import unittest

from simpul_extract.storage import (
    InMemoryUpsertStore,
    PostgrestUpsertStore,
    StorageError,
    UpsertResult,
    UpsertStore,
    postgrest_store_from_env,
)


class _CountingClock:
    """Deterministische fetched_at-generator: elke aanroep levert een nieuwe,
    oplopende waarde, zodat een test kan bewijzen dat fetched_at ververst
    wordt zonder aan een echte klok te hangen."""

    def __init__(self):
        self._calls = 0

    def __call__(self):
        self._calls += 1
        return f"2026-08-29T00:00:{self._calls:02d}+00:00"


class TestUpsertStoreInterface(unittest.TestCase):
    def test_kale_interface_werpt_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            UpsertStore().upsert("customer", [])

    def test_inmemory_store_voldoet_aan_de_interface(self):
        self.assertIsInstance(InMemoryUpsertStore(), UpsertStore)

    def test_postgrest_store_voldoet_aan_de_interface(self):
        self.assertIsInstance(
            PostgrestUpsertStore(base_url="https://example.invalid", api_key="geheim", session=_RecordingPostgrestSession()),
            UpsertStore,
        )


class TestUpsertIsIdempotent(unittest.TestCase):
    def test_tweemaal_dezelfde_rij_levert_een_rij_op_met_bijgewerkte_waarde(self):
        store = InMemoryUpsertStore(now=_CountingClock())

        store.upsert("customer", [{"id": 1, "title": "Oude naam"}])
        store.upsert("customer", [{"id": 1, "title": "Nieuwe naam"}])

        rows = store.tables["customer"]
        self.assertEqual(
            len(rows), 1,
            "twee schrijfacties met dezelfde id leverden meer dan een rij op",
        )
        self.assertEqual(rows[1]["title"], "Nieuwe naam")

    def test_gewijzigd_veld_telt_als_update_niet_als_insert(self):
        store = InMemoryUpsertStore(now=_CountingClock())
        store.upsert("customer", [{"id": 1, "title": "Oud"}])

        resultaat = store.upsert("customer", [{"id": 1, "title": "Nieuw"}])

        self.assertEqual(resultaat, UpsertResult(inserted=0, updated=1))

    def test_ongewijzigde_ronde_voegt_niets_toe_en_wijzigt_niets(self):
        store = InMemoryUpsertStore(now=_CountingClock())
        rows = [
            {"id": 1, "title": "Klant een"},
            {"id": 2, "title": "Klant twee"},
        ]

        eerste_ronde = store.upsert("customer", rows)
        self.assertEqual(eerste_ronde, UpsertResult(inserted=2, updated=0))

        tweede_ronde = store.upsert("customer", rows)
        self.assertEqual(
            tweede_ronde, UpsertResult(inserted=0, updated=0),
            "een tweede, ongewijzigde ronde had nul inserts en nul updates moeten opleveren",
        )
        self.assertEqual(len(store.tables["customer"]), 2)

    def test_rij_zonder_id_wordt_geweigerd(self):
        store = InMemoryUpsertStore()
        with self.assertRaises(StorageError):
            store.upsert("customer", [{"title": "Zonder id"}])


class TestElkeRijDraagtFetchedAt(unittest.TestCase):
    def test_fetched_at_staat_op_elke_geschreven_rij(self):
        store = InMemoryUpsertStore(now=_CountingClock())

        store.upsert("supplier", [{"id": 501, "name": "Leverancier"}])

        row = store.tables["supplier"][501]
        self.assertIn("fetched_at", row)
        self.assertIsNotNone(row["fetched_at"])

    def test_fetched_at_ververst_ook_als_de_rij_zelf_niet_verandert(self):
        store = InMemoryUpsertStore(now=_CountingClock())
        row = {"id": 1, "title": "Klant een"}

        store.upsert("customer", [row])
        eerste_fetched_at = store.tables["customer"][1]["fetched_at"]
        store.upsert("customer", [row])
        tweede_fetched_at = store.tables["customer"][1]["fetched_at"]

        self.assertNotEqual(eerste_fetched_at, tweede_fetched_at)


class _StubPostgrestResponse:
    def raise_for_status(self):
        return None


class _RecordingPostgrestSession:
    """Netwerkloze stub voor de echte PostgREST-implementatie: registreert
    elk verzoek maar verlaat het proces nooit. Bewijst dat de echte
    implementatie ON CONFLICT (id) DO UPDATE aanvraagt, zonder ooit het
    echte Supabase-project te raken (durable regel 5)."""

    def __init__(self):
        self.calls = []

    def post(self, url, json=None, params=None, headers=None):
        self.calls.append({"url": url, "json": json, "params": params, "headers": headers})
        return _StubPostgrestResponse()


class TestPostgrestUpsertStoreBestaatEnGebruiktOnConflict(unittest.TestCase):
    def test_post_naar_schema_simpul_raw_met_on_conflict_id(self):
        session = _RecordingPostgrestSession()
        store = PostgrestUpsertStore(
            base_url="https://example.invalid/rest/v1",
            api_key="geheim",
            session=session,
            now=lambda: "2026-08-29T00:00:00+00:00",
        )

        store.upsert("customer", [{"id": 1, "title": "Klant"}])

        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call["url"], "https://example.invalid/rest/v1/customer")
        self.assertEqual(call["params"], {"on_conflict": "id"})
        self.assertIn("merge-duplicates", call["headers"]["Prefer"])
        self.assertEqual(call["headers"]["Content-Profile"], "simpul_raw")
        self.assertEqual(call["json"][0]["fetched_at"], "2026-08-29T00:00:00+00:00")

    def test_geen_rijen_doet_geen_verzoek(self):
        session = _RecordingPostgrestSession()
        store = PostgrestUpsertStore(base_url="https://example.invalid", api_key="geheim", session=session)

        store.upsert("customer", [])

        self.assertEqual(session.calls, [])


class TestPostgrestStoreFromEnv(unittest.TestCase):
    def test_vereist_beide_env_variabelen(self):
        with self.assertRaises(StorageError):
            postgrest_store_from_env({})

    def test_bouwt_store_uit_env(self):
        # De projecturl zoals Supabase hem uitgeeft: zonder pad. Dat `/rest/v1`
        # er hier bijkomt, toetst tests/test_postgrest_seam.py.
        store = postgrest_store_from_env({
            "SUPABASE_URL": "https://example.invalid",
            "SUPABASE_SECRET_KEY": "geheim",
        })
        self.assertIsInstance(store, PostgrestUpsertStore)


if __name__ == "__main__":
    unittest.main()
