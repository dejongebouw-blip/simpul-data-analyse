"""Toetst de auditregel (issue 08): elke ronde schrijft per entiteit één
regel naar `simpul_raw.extraction_run` — met `run_id`, `started_at`,
`finished_at`, `entity`, `rows_stored`, `source_total` en `note` — via
dezelfde upsert-interface als de entiteitstabellen zelf. De regel wordt ook
geschreven wanneer de ronde faalt op volledigheid; `note` zegt dan wat er
misging.

`complete` staat er bewust NIET bij: die kolom is
`generated always as (source_total is not null and rows_stored =
source_total) stored`, dus de database rekent hem uit de twee getallen in
dezelfde rij. Wat de schrijflaag hier wél of niet aanlevert, staat gepind in
`tests/test_postgrest_seam.py`.

Alles draait op `tests._stubs` en de in-memory nepimplementatie uit issue
07; er gaat geen verzoek naar een draaiende omgeving.
"""
from __future__ import annotations

import unittest

from simpul_extract.completeness import (
    EXTRACTION_RUN_TABLE,
    CompletenessError,
    run_entity_round,
)
from simpul_extract.paginate import paginate
from simpul_extract.storage import InMemoryUpsertStore
from tests._stubs import fractal_rows_pages, laravel_rows_pages, make_paginate_client


def _identity(items):
    return list(items)


class _CountingClock:
    """Deterministische klok: elke aanroep levert een nieuwe, oplopende
    waarde, zodat een test kan bewijzen dat started_at en finished_at
    verschillen zonder aan een echte klok te hangen."""

    def __init__(self):
        self._calls = 0

    def __call__(self):
        self._calls += 1
        return f"2026-08-29T00:00:{self._calls:02d}+00:00"


def _round(entity, path, pages_payload, *, run_id, store=None):
    client, _ = make_paginate_client(pages_payload, path=path)
    store = store if store is not None else InMemoryUpsertStore(now=_CountingClock())
    return run_entity_round(
        entity=entity,
        table=entity,
        pages=paginate(client, path),
        parse=_identity,
        store=store,
        run_id=run_id,
        now=_CountingClock(),
    ), store


class TestAuditregelBijGeslaagdeRonde(unittest.TestCase):
    def test_schrijft_een_regel_met_alle_verplichte_velden(self):
        _, store = _round(
            "customer", "/customer/all.json",
            fractal_rows_pages(120, total=120, per_page=120),
            run_id="run-abc",
        )

        rows = store.tables[EXTRACTION_RUN_TABLE]
        self.assertEqual(len(rows), 1)
        (row,) = rows.values()
        self.assertEqual(row["run_id"], "run-abc")
        self.assertEqual(row["entity"], "customer")
        self.assertEqual(row["rows_stored"], 120)
        self.assertEqual(row["source_total"], 120)
        self.assertIsNone(row["note"], "een volledige ronde heeft niets uit te leggen")
        self.assertNotIn(
            "complete", row,
            "complete is een generated column; aanleveren geeft PostgREST-fout 428C9",
        )
        self.assertIsNotNone(row["started_at"])
        self.assertIsNotNone(row["finished_at"])


class TestAuditregelBijOnvolledigeRonde(unittest.TestCase):
    def test_wordt_ook_geschreven_als_de_controle_faalt_met_een_verklarende_note(self):
        store = InMemoryUpsertStore(now=_CountingClock())

        with self.assertRaises(CompletenessError):
            _round(
                "customer", "/customer/all.json",
                fractal_rows_pages(900, total=951, per_page=900),
                run_id="run-fout",
                store=store,
            )

        rows = store.tables[EXTRACTION_RUN_TABLE]
        self.assertEqual(
            len(rows), 1,
            "de auditregel moet er staan, ook al is de ronde onvolledig",
        )
        (row,) = rows.values()
        self.assertEqual(row["rows_stored"], 900)
        self.assertEqual(row["source_total"], 951)
        self.assertEqual(row["note"], "900 weggeschreven, bron meldt 951")
        self.assertNotIn("complete", row)


class TestEenRunIdPerRondeDrieRegelsErbij(unittest.TestCase):
    def test_drie_entiteiten_delen_run_id_maar_hebben_elk_hun_eigen_regel(self):
        store = InMemoryUpsertStore(now=_CountingClock())
        run_id = "run-gedeeld"

        _round("customer", "/customer/all.json", fractal_rows_pages(4, total=4, per_page=4), run_id=run_id, store=store)
        _round("project", "/project/all.json", fractal_rows_pages(6, total=6, per_page=6), run_id=run_id, store=store)
        _round("supplier", "/supplier.json", laravel_rows_pages(3, total=3, per_page=3), run_id=run_id, store=store)

        rows = store.tables[EXTRACTION_RUN_TABLE]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["run_id"] == run_id for row in rows.values()))
        self.assertEqual(
            {row["entity"] for row in rows.values()}, {"customer", "project", "supplier"}
        )


if __name__ == "__main__":
    unittest.main()
