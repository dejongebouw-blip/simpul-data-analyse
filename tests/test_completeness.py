"""Toetst de volledigheidscontrole (issue 08): het aantal weggeschreven
rijen wordt per entiteit vergeleken met het totaal dat de bron zelf meldt,
en niet met een in de code vastgelegde peilwaarde.

Reproductie eerst: een pagineerstub die `total: 951` meldt maar op de
laatste pagina stopt bij 900 rijen. Zolang de volledigheidscontrole
ontbreekt, eindigt de ronde zonder fout op een dataset die 51 rijen mist —
dat maakt
`test_tekort_werpt_completenesserror_met_entiteit_aantal_en_totaal` rood.
Groen betekent: `CompletenessError`, met de entiteit, 900 en 951 in de
melding, en een niet-nul `exit_code`.

Alles draait op `tests._stubs` en de in-memory nepimplementatie uit issue
07; geen enkel testgeval doet een echt netwerkverzoek of een schrijfactie
naar een draaiende omgeving.
"""
from __future__ import annotations

import unittest

from simpul_extract.completeness import (
    EXIT_INCOMPLETE,
    EXIT_OK,
    CompletenessError,
    run_entity_round,
)
from simpul_extract.paginate import paginate
from simpul_extract.storage import InMemoryUpsertStore
from tests._stubs import fractal_page, fractal_rows_pages, make_paginate_client


def _identity(items):
    return list(items)


def _run(row_count, total, *, per_page=None, run_id="run-1", entity="customer"):
    pages_payload = fractal_rows_pages(row_count, total=total, per_page=per_page or max(row_count, 1))
    client, _ = make_paginate_client(pages_payload, path="/customer/all.json")
    store = InMemoryUpsertStore()
    return run_entity_round(
        entity=entity,
        table="customer",
        pages=paginate(client, "/customer/all.json"),
        parse=_identity,
        store=store,
        run_id=run_id,
    ), store


class TestVolledigheidscontroleFaaltOpTekort(unittest.TestCase):
    """Tegen-pin: `total` meldt 951, de bron levert feitelijk 900 rijen."""

    def test_tekort_werpt_completenesserror_met_entiteit_aantal_en_totaal(self):
        with self.assertRaises(CompletenessError) as ctx:
            _run(900, total=951)

        error = ctx.exception
        self.assertEqual(error.entity, "customer")
        self.assertEqual(error.rows_stored, 900)
        self.assertEqual(error.source_total, 951)

        melding = str(error)
        self.assertIn("customer", melding)
        self.assertIn("900", melding)
        self.assertIn("951", melding)

    def test_tekort_levert_een_niet_nul_exit_code_op(self):
        self.assertNotEqual(EXIT_INCOMPLETE, 0)

        with self.assertRaises(CompletenessError) as ctx:
            _run(900, total=951)

        self.assertEqual(ctx.exception.exit_code, EXIT_INCOMPLETE)


class TestVolledigheidscontroleSlaagtOpJuistAantal(unittest.TestCase):
    def test_kloppend_aantal_levert_exit_ok_op_zonder_te_werpen(self):
        exit_code, _ = _run(951, total=951)

        self.assertEqual(exit_code, EXIT_OK)

    def test_controle_gebruikt_het_door_de_bron_gemelde_totaal_niet_een_vaste_peilwaarde(self):
        """Dezelfde code moet even goed slagen op een heel ander aantal —
        de bron mag groeien zonder dat dit codepad daarop is dichtgetimmerd."""
        exit_code, _ = _run(1200, total=1200)

        self.assertEqual(exit_code, EXIT_OK)


class TestOntbrekendTotaalGeldtAlsOnvolledig(unittest.TestCase):
    def test_geen_enkele_pagina_meldt_een_totaal(self):
        # fractal_rows_pages() vult een ontbrekend `total` zelf aan met de
        # rijtelling; om een bron te simuleren die écht geen totaal meldt,
        # bouwen we de pagina hier direct op met `total=None`.
        payload = {
            1: fractal_page(
                data=[{"id": i} for i in range(10)],
                current_page=1,
                total_pages=1,
                total=None,
            )
        }
        client, _ = make_paginate_client(payload, path="/customer/all.json")
        store = InMemoryUpsertStore()

        with self.assertRaises(CompletenessError) as ctx:
            run_entity_round(
                entity="customer",
                table="customer",
                pages=paginate(client, "/customer/all.json"),
                parse=_identity,
                store=store,
                run_id="run-1",
            )

        self.assertIsNone(ctx.exception.source_total)
        self.assertEqual(ctx.exception.rows_stored, 10)


if __name__ == "__main__":
    unittest.main()
