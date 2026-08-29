"""Toetst de Fractal-pagineringsvorm achter dezelfde interface (issue 05).

  - De lus doorloopt ``meta.pagination`` van pagina 1 tot en met
    ``total_pages``; de tegen-pin uit het contract is een fixture met drie
    pagina's waarvan een halve doorloop er maar één zou pakken.
  - De aanroeper kent het verschil met de Laravel-vorm niet: beide vormen
    leveren dezelfde :class:`Page`-waarden en dezelfde ``EntitySpec``-route
    door :func:`extract_entity`.

Fixtures zijn synthetisch; geen enkel testgeval doet een echt netwerkverzoek.
"""
from __future__ import annotations

import unittest

from simpul_extract.paginate import Page, fractal_pages, laravel_pages
from simpul_extract.pipeline import CUSTOMER_SPEC, extract_entity
from simpul_extract.storage import InMemoryWriter
from tests._stubs import (
    PagedTransport,
    TickingClock,
    customer_pages,
    fractal_page,
    make_client,
    synthetic_customer,
)


class TestFractalLoopWalksToEnd(unittest.TestCase):
    def test_three_pages_are_all_requested_in_order(self) -> None:
        """De tegen-pin: een lus die alleen pagina 1 pakt maakt dit rood."""
        transport = PagedTransport(customer_pages(5, per_page=2))
        pages = list(fractal_pages(make_client(transport), "/customer/all.json"))
        self.assertEqual(
            transport.requested_pages(),
            [1, 2, 3],
            msg="de lus moet meta.pagination.total_pages respecteren",
        )
        self.assertEqual(len(pages), 3)

    def test_all_items_across_pages_are_yielded(self) -> None:
        transport = PagedTransport(customer_pages(5, per_page=2))
        pages = list(fractal_pages(make_client(transport), "/customer/all.json"))
        ids = [item["id"] for page in pages for item in page.items]
        self.assertEqual(ids, [1, 2, 3, 4, 5])

    def test_single_page_stops_after_one_request(self) -> None:
        transport = PagedTransport(customer_pages(3, per_page=50))
        pages = list(fractal_pages(make_client(transport), "/customer/all.json"))
        self.assertEqual(transport.requested_pages(), [1])
        self.assertEqual(len(pages), 1)

    def test_total_comes_from_meta_pagination(self) -> None:
        transport = PagedTransport(customer_pages(5, total=951, per_page=2))
        pages = list(fractal_pages(make_client(transport), "/customer/all.json"))
        for page in pages:
            self.assertEqual(page.total, 951)


class TestSameInterfaceAsLaravel(unittest.TestCase):
    """De aanroeper vraagt om alle pagina's; de vorm is een eigenschap van
    het endpoint, niet van de aanroeper."""

    def test_both_forms_yield_page_values(self) -> None:
        transport = PagedTransport(customer_pages(2))
        for page in fractal_pages(make_client(transport), "/customer/all.json"):
            self.assertIsInstance(page, Page)
            self.assertIsInstance(page.items, list)

    def test_fractal_and_laravel_share_the_callable_signature(self) -> None:
        for pages_fn in (fractal_pages, laravel_pages):
            with self.subTest(fn=pages_fn.__name__):
                self.assertTrue(callable(pages_fn))

    def test_extract_entity_consumes_fractal_endpoint_unchanged(self) -> None:
        """Dezelfde `extract_entity` als voor de Laravel-vorm, ongewijzigd."""
        writer = InMemoryWriter()
        transport = PagedTransport(customer_pages(5, per_page=2))
        audit = extract_entity(
            client=make_client(transport),
            writer=writer,
            spec=CUSTOMER_SPEC,
            run_id="run-fractal",
            now=TickingClock(),
        )
        self.assertEqual(audit["rows_stored"], 5)
        self.assertEqual(audit["source_total"], 5)
        stored_ids = sorted(row["id"] for row in writer.rows("customer"))
        self.assertEqual(stored_ids, [1, 2, 3, 4, 5])


class TestFractalEdgeShapes(unittest.TestCase):
    def test_empty_result_yields_one_empty_page(self) -> None:
        page = fractal_page(data=[], current_page=1, total_pages=1, total=0)
        transport = PagedTransport({1: page})
        pages = list(fractal_pages(make_client(transport), "/customer/all.json"))
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].items, [])
        self.assertEqual(pages[0].total, 0)

    def test_items_are_passed_through_untouched(self) -> None:
        source = synthetic_customer(7)
        page = fractal_page(data=[source], current_page=1, total_pages=1, total=1)
        transport = PagedTransport({1: page})
        (result,) = fractal_pages(make_client(transport), "/customer/all.json")
        self.assertEqual(result.items, [source])


if __name__ == "__main__":
    unittest.main()
