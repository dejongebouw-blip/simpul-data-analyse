"""End-to-end over drie entiteiten: relaties, projecten, leveranciers (issue 05).

  - Eén commando (:func:`simpul_extract.pipeline.run` met de standaardspecs)
    doorloopt de drie entiteiten die dit account mag zien en schrijft ze via
    de schrijfnaad weg.
  - Relaties landen in ``customer`` en projecten in ``project``, met ``id``
    als sleutel; het genest meegeleverde relatie-object landt genormaliseerd
    op ``project.customer_``-kolommen en niet als aparte relatie.
  - De volledigheidscontrole geldt voor alle drie; de eindrapportage toont
    per entiteit opgeslagen tegen gemeld totaal.

Fixtures zijn handgemaakt en synthetisch, met een handvol rijen; geen enkel
testgeval doet een echt netwerkverzoek.
"""
from __future__ import annotations

import io
import unittest

from simpul_extract.customers import CUSTOMER_COLUMNS
from simpul_extract.pipeline import DEFAULT_SPECS, run
from simpul_extract.projects import PROJECT_COLUMNS
from simpul_extract.storage import InMemoryWriter
from tests._stubs import (
    OnlyUpsertWriter,
    RoutedTransport,
    TickingClock,
    customer_pages,
    make_client,
    project_pages,
    supplier_pages,
    synthetic_customer,
    synthetic_project,
)


def _routes(**overrides):
    routes = {
        "/customer/all.json": customer_pages(4, per_page=2),
        "/project/all.json": project_pages(3, per_page=2),
        "/supplier.json": supplier_pages(2, per_page=2),
    }
    routes.update(overrides)
    return routes


def _run(routes, **kwargs):
    writer = kwargs.pop("writer", InMemoryWriter())
    stdout = kwargs.pop("stdout", io.StringIO())
    stderr = kwargs.pop("stderr", io.StringIO())
    exit_code = run(
        client=make_client(RoutedTransport(routes)),
        writer=writer,
        run_id=kwargs.pop("run_id", "run-full"),
        now=kwargs.pop("now", TickingClock()),
        stdout=stdout,
        stderr=stderr,
        **kwargs,
    )
    return exit_code, writer, stdout, stderr


class TestOneCommandWalksThreeEntities(unittest.TestCase):
    def test_default_specs_cover_exactly_the_three_entities(self) -> None:
        self.assertEqual(
            [spec.entity for spec in DEFAULT_SPECS],
            ["customer", "project", "supplier"],
        )

    def test_run_populates_all_three_tables_and_exits_zero(self) -> None:
        exit_code, writer, _, stderr = _run(_routes())
        self.assertEqual(exit_code, 0, msg=stderr.getvalue())
        self.assertEqual(len(writer.rows("customer")), 4)
        self.assertEqual(len(writer.rows("project")), 3)
        self.assertEqual(len(writer.rows("supplier")), 2)

    def test_everything_goes_through_the_write_seam(self) -> None:
        writer = OnlyUpsertWriter()
        exit_code, _, _, _ = _run(_routes(), writer=writer)
        self.assertEqual(exit_code, 0)
        tables = {table for table, _ in writer.calls}
        self.assertEqual(
            tables,
            {"customer", "project", "supplier", "extraction_run"},
            msg="alle schrijfacties horen door upsert(table, rows) te gaan",
        )

    def test_each_endpoint_is_paginated_to_the_end(self) -> None:
        routes = _routes()
        transport = RoutedTransport(routes)
        exit_code = run(
            client=make_client(transport),
            writer=InMemoryWriter(),
            run_id="run-pages",
            now=TickingClock(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            transport.requested_pages_for("/customer/all.json"), [1, 2]
        )
        self.assertEqual(
            transport.requested_pages_for("/project/all.json"), [1, 2]
        )
        self.assertEqual(transport.requested_pages_for("/supplier.json"), [1])

    def test_audit_rows_for_all_three_entities(self) -> None:
        _, writer, _, _ = _run(_routes())
        audit = writer.rows("extraction_run")
        self.assertEqual(
            sorted(row["entity"] for row in audit),
            ["customer", "project", "supplier"],
        )


class TestCustomerRowsMatchSchema(unittest.TestCase):
    def test_columns_and_fetched_at(self) -> None:
        _, writer, _, _ = _run(_routes())
        expected = set(CUSTOMER_COLUMNS) | {"fetched_at"}
        for row in writer.rows("customer"):
            self.assertEqual(set(row), expected)
            self.assertTrue(row["fetched_at"])

    def test_field_values_map_one_to_one(self) -> None:
        _, writer, _, _ = _run(_routes())
        rows = {row["id"]: row for row in writer.rows("customer")}
        source = synthetic_customer(1)
        for column in CUSTOMER_COLUMNS:
            self.assertEqual(rows[1][column], source[column])

    def test_id_is_the_key_on_rerun(self) -> None:
        writer = InMemoryWriter()
        clock = TickingClock()
        for run_id in ("run-1", "run-2"):
            _run(_routes(), writer=writer, run_id=run_id, now=clock)
        self.assertEqual(
            len(writer.rows("customer")),
            4,
            msg="tweemaal dezelfde relatie-id moet één rij opleveren",
        )
        self.assertEqual(len(writer.rows("project")), 3)


class TestNestedCustomerIsNormalised(unittest.TestCase):
    def test_project_rows_carry_customer_prefixed_columns(self) -> None:
        _, writer, _, _ = _run(_routes())
        expected = set(PROJECT_COLUMNS) | {"fetched_at"}
        for row in writer.rows("project"):
            self.assertEqual(set(row), expected)
        rows = {row["id"]: row for row in writer.rows("project")}
        source = synthetic_project(1)
        self.assertEqual(rows[1]["customer_name"], source["customer"]["name"])
        self.assertEqual(rows[1]["customer_city"], source["customer"]["city"])

    def test_no_customer_id_link_is_invented(self) -> None:
        _, writer, _, _ = _run(_routes())
        for row in writer.rows("project"):
            self.assertNotIn(
                "customer_id",
                row,
                msg="de bron-JSON bevat geen relatie-id; verzin er geen",
            )
            self.assertNotIn("customer", row)

    def test_nested_customer_is_not_stored_as_separate_customer_row(self) -> None:
        """De tegen-pin: schrijf het geneste object als aparte rij in
        ``customer`` weg en deze test wordt rood op een relatie die niet
        uit de relatielijst komt."""
        _, writer, _, _ = _run(_routes())
        customer_names = {row["name"] for row in writer.rows("customer")}
        expected_names = {synthetic_customer(i)["name"] for i in range(1, 5)}
        self.assertEqual(
            customer_names,
            expected_names,
            msg="tabel customer mag alleen rijen uit de relatielijst bevatten",
        )
        self.assertEqual(len(writer.rows("customer")), 4)


class TestCompletenessAppliesToAllThree(unittest.TestCase):
    def _shortfall_case(self, name, routes):
        exit_code, _, _, stderr = _run(routes)
        self.assertNotEqual(exit_code, 0, msg=f"tekort bij {name} moet falen")
        self.assertIn(name, stderr.getvalue())

    def test_customer_shortfall_fails(self) -> None:
        self._shortfall_case(
            "customer",
            _routes(**{"/customer/all.json": customer_pages(4, total=951, per_page=2)}),
        )

    def test_project_shortfall_fails(self) -> None:
        self._shortfall_case(
            "project",
            _routes(**{"/project/all.json": project_pages(3, total=2793, per_page=2)}),
        )

    def test_supplier_shortfall_fails(self) -> None:
        self._shortfall_case(
            "supplier",
            _routes(**{"/supplier.json": supplier_pages(2, total=9, per_page=2)}),
        )

    def test_shortfall_in_one_entity_still_extracts_the_others(self) -> None:
        routes = _routes(
            **{"/project/all.json": project_pages(3, total=2793, per_page=2)}
        )
        exit_code, writer, _, _ = _run(routes)
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(len(writer.rows("customer")), 4)
        self.assertEqual(len(writer.rows("supplier")), 2)


class TestFinalReportPerEntity(unittest.TestCase):
    def test_report_shows_stored_versus_reported_total(self) -> None:
        exit_code, _, stdout, _ = _run(_routes())
        self.assertEqual(exit_code, 0)
        report = stdout.getvalue()
        self.assertIn("customer: 4 opgeslagen van 4 gemeld", report)
        self.assertIn("project: 3 opgeslagen van 3 gemeld", report)
        self.assertIn("supplier: 2 opgeslagen van 2 gemeld", report)

    def test_report_shows_shortfall_numbers(self) -> None:
        routes = _routes(
            **{"/project/all.json": project_pages(3, total=2793, per_page=2)}
        )
        exit_code, _, stdout, stderr = _run(routes)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("project: 3 opgeslagen van 2793 gemeld", stdout.getvalue())
        self.assertIn("2793", stderr.getvalue())
        self.assertIn("3", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
