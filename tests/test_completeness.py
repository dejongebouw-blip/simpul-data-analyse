"""Toetst SC-2 (volledigheid) en SC-3 (idempotentie) voor de doorsnede.

Tegen-pin uit het contract: een fixture die ``total`` op 951 zet maar 900
rijen levert moet deze module rood maken zolang de volledigheidscontrole
ontbreekt, en groen zodra die er is. Voor idempotentie: een schrijver die
rijen aanhangt in plaats van op ``id`` samen te voegen maakt de tests rood.

Fixtures zijn synthetisch; geen enkel testgeval doet een echt netwerkverzoek.
"""
from __future__ import annotations

import io
import subprocess
import sys
import unittest

from simpul_extract.pipeline import (
    SUPPLIER_SPEC,
    CompletenessError,
    extract_entity,
    run,
)
from simpul_extract.storage import InMemoryWriter
from tests._stubs import (
    PagedTransport,
    TickingClock,
    make_client,
    supplier_pages,
    synthetic_supplier,
)


class TestUpsertIsIdempotent(unittest.TestCase):
    def test_same_id_twice_yields_one_row_with_updated_fields(self) -> None:
        writer = InMemoryWriter()
        original = synthetic_supplier(1)
        writer.upsert("supplier", [original])
        changed = dict(original, name="Nieuwe naam BV", city="Anderstad")
        writer.upsert("supplier", [changed])
        rows = writer.rows("supplier")
        self.assertEqual(
            len(rows),
            1,
            msg="tweemaal dezelfde id moet één rij opleveren, geen duplicaat",
        )
        self.assertEqual(rows[0]["name"], "Nieuwe naam BV")
        self.assertEqual(rows[0]["city"], "Anderstad")

    def test_distinct_ids_are_both_kept(self) -> None:
        writer = InMemoryWriter()
        writer.upsert("supplier", [synthetic_supplier(1)])
        writer.upsert("supplier", [synthetic_supplier(2)])
        self.assertEqual(len(writer.rows("supplier")), 2)


class TestSecondRunUpdatesWithoutDuplicates(unittest.TestCase):
    def test_rerun_on_unchanged_source_keeps_row_count(self) -> None:
        writer = InMemoryWriter()
        clock = TickingClock()
        for run_id in ("run-1", "run-2"):
            transport = PagedTransport(supplier_pages(5, per_page=2))
            extract_entity(
                client=make_client(transport),
                writer=writer,
                spec=SUPPLIER_SPEC,
                run_id=run_id,
                now=clock,
            )
        self.assertEqual(len(writer.rows("supplier")), 5)

    def test_rerun_updates_changed_fields_and_fetched_at(self) -> None:
        writer = InMemoryWriter()
        clock = TickingClock()
        extract_entity(
            client=make_client(PagedTransport(supplier_pages(3))),
            writer=writer,
            spec=SUPPLIER_SPEC,
            run_id="run-1",
            now=clock,
        )
        first_fetched = {
            row["id"]: row["fetched_at"] for row in writer.rows("supplier")
        }

        def rename_one(row):
            if row["id"] == 1:
                row["name"] = "Hernoemd BV"

        extract_entity(
            client=make_client(
                PagedTransport(supplier_pages(3, mutate=rename_one))
            ),
            writer=writer,
            spec=SUPPLIER_SPEC,
            run_id="run-2",
            now=clock,
        )
        rows = {row["id"]: row for row in writer.rows("supplier")}
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["name"], "Hernoemd BV")
        self.assertGreater(
            rows[1]["fetched_at"],
            first_fetched[1],
            msg="een tweede run hoort fetched_at te verversen",
        )

    def test_each_run_appends_its_own_audit_row(self) -> None:
        writer = InMemoryWriter()
        clock = TickingClock()
        for run_id in ("run-1", "run-2"):
            extract_entity(
                client=make_client(PagedTransport(supplier_pages(2))),
                writer=writer,
                spec=SUPPLIER_SPEC,
                run_id=run_id,
                now=clock,
            )
        audit = writer.rows("extraction_run")
        self.assertEqual(len(audit), 2)
        self.assertEqual([row["run_id"] for row in audit], ["run-1", "run-2"])


class TestShortfallFailsTheRun(unittest.TestCase):
    """De tegen-pin voor SC-2: total 951, geleverd 900 → rood zonder controle."""

    def _short_transport(self) -> PagedTransport:
        # 18 volle pagina's van 50 = 900 rijen, terwijl de bron 951 meldt.
        return PagedTransport(supplier_pages(900, total=951, per_page=50))

    def test_extract_raises_completeness_error(self) -> None:
        with self.assertRaises(CompletenessError) as cm:
            extract_entity(
                client=make_client(self._short_transport()),
                writer=InMemoryWriter(),
                spec=SUPPLIER_SPEC,
                run_id="run-short",
                now=TickingClock(),
            )
        message = str(cm.exception)
        self.assertIn("supplier", message, msg="melding moet de entiteit noemen")
        self.assertIn("951", message, msg="melding moet het verwachte totaal noemen")
        self.assertIn("900", message, msg="melding moet het gevonden aantal noemen")

    def test_run_returns_nonzero_and_reports_on_stderr(self) -> None:
        stderr = io.StringIO()
        exit_code = run(
            client=make_client(self._short_transport()),
            writer=InMemoryWriter(),
            specs=(SUPPLIER_SPEC,),
            run_id="run-short",
            now=TickingClock(),
            stdout=io.StringIO(),
            stderr=stderr,
        )
        self.assertNotEqual(exit_code, 0)
        message = stderr.getvalue()
        self.assertIn("supplier", message)
        self.assertIn("951", message)
        self.assertIn("900", message)

    def test_audit_row_is_written_even_on_shortfall(self) -> None:
        writer = InMemoryWriter()
        with self.assertRaises(CompletenessError):
            extract_entity(
                client=make_client(self._short_transport()),
                writer=writer,
                spec=SUPPLIER_SPEC,
                run_id="run-short",
                now=TickingClock(),
            )
        (audit,) = writer.rows("extraction_run")
        self.assertEqual(audit["rows_stored"], 900)
        self.assertEqual(audit["source_total"], 951)


class TestCompleteRunSucceeds(unittest.TestCase):
    def test_matching_total_returns_zero(self) -> None:
        stderr = io.StringIO()
        exit_code = run(
            client=make_client(PagedTransport(supplier_pages(5, per_page=2))),
            writer=InMemoryWriter(),
            specs=(SUPPLIER_SPEC,),
            run_id="run-ok",
            now=TickingClock(),
            stdout=io.StringIO(),
            stderr=stderr,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")


class TestProcessExitsNonZeroOnShortfall(unittest.TestCase):
    """Een afwijking moet de run als proces laten falen met een niet-nul
    exit code — niet alleen een exception binnen het testproces."""

    def test_process_exit_code_and_message(self) -> None:
        script = (
            "import sys\n"
            "from simpul_extract.pipeline import SUPPLIER_SPEC, run\n"
            "from simpul_extract.storage import InMemoryWriter\n"
            "from tests._stubs import PagedTransport, make_client, supplier_pages\n"
            "transport = PagedTransport(supplier_pages(41, total=43, per_page=10))\n"
            "sys.exit(run(client=make_client(transport), writer=InMemoryWriter(),\n"
            "             specs=(SUPPLIER_SPEC,)))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(
            proc.returncode,
            0,
            msg=(
                "verwacht niet-nul exit code bij een tekort aan rijen; "
                f"kreeg {proc.returncode}.\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            ),
        )
        self.assertIn("supplier", proc.stderr)
        self.assertIn("43", proc.stderr)
        self.assertIn("41", proc.stderr)


if __name__ == "__main__":
    unittest.main()
