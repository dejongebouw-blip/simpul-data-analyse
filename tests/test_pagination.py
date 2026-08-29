"""Toetst dat één pagineerinterface beide vormen tot het einde doorloopt
(issue 05): Fractal (`meta.pagination`, gebruikt door `/customer/all.json`
en `/project/all.json`) en de Laravel-paginator (`current_page`/
`last_page`, gebruikt door `/supplier.json`).

De aanroeper roept in beide gevallen dezelfde functie aan met dezelfde
argumenten: de vorm is een eigenschap van de respons, niet van de
aanroep. Alles draait op `tests._stubs.PagedSession`; geen enkel testgeval
doet een echt netwerkverzoek.
"""
from __future__ import annotations

import unittest

from simpul_extract.paginate import Page, paginate
from tests._stubs import (
    fractal_page,
    fractal_rows_pages,
    laravel_page,
    laravel_rows_pages,
    make_paginate_client,
)


class TestFractalFormWalksToEnd(unittest.TestCase):
    """Fractal-vorm: `/customer/all.json`, `/project/all.json`."""

    def test_three_pages_are_all_requested_in_order(self) -> None:
        """De tegen-pin: een lus die alleen pagina 1 pakt maakt dit rood
        (50 in plaats van 150 rijen)."""
        pages_payload = fractal_rows_pages(150, per_page=50)
        client, session = make_paginate_client(pages_payload, path="/customer/all.json")

        pages = list(paginate(client, "/customer/all.json"))

        self.assertEqual(session.requested_pages(), [1, 2, 3])
        self.assertEqual(len(pages), 3)

    def test_all_items_across_pages_are_yielded_in_order_without_overlap(self) -> None:
        pages_payload = fractal_rows_pages(150, per_page=50, start_id=32475)
        client, _ = make_paginate_client(pages_payload, path="/customer/all.json")

        ids = [item["id"] for page in paginate(client, "/customer/all.json") for item in page.items]

        self.assertEqual(ids, list(range(32475, 32475 + 150)))
        self.assertEqual(len(ids), len(set(ids)), "geen duplicaten toegestaan")

    def test_single_page_stops_after_one_request(self) -> None:
        pages_payload = fractal_rows_pages(3, per_page=50)
        client, session = make_paginate_client(pages_payload, path="/project/all.json")

        pages = list(paginate(client, "/project/all.json"))

        self.assertEqual(session.requested_pages(), [1])
        self.assertEqual(len(pages), 1)

    def test_reported_total_is_readable_from_the_response(self) -> None:
        pages_payload = fractal_rows_pages(150, total=951, per_page=50)
        client, _ = make_paginate_client(pages_payload, path="/customer/all.json")

        for page in paginate(client, "/customer/all.json"):
            self.assertEqual(page.total, 951)

    def test_empty_result_yields_one_empty_page(self) -> None:
        pages_payload = fractal_rows_pages(0, total=0, per_page=50)
        client, _ = make_paginate_client(pages_payload, path="/customer/all.json")

        pages = list(paginate(client, "/customer/all.json"))

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].items, [])
        self.assertEqual(pages[0].total, 0)


class TestLaravelFormWalksToEnd(unittest.TestCase):
    """Laravel-paginatorvorm: `/supplier.json`."""

    def test_three_pages_are_all_requested_in_order(self) -> None:
        """Dezelfde tegen-pin als bij Fractal: een halve doorloop levert
        50 in plaats van 150 rijen."""
        pages_payload = laravel_rows_pages(150, per_page=50)
        client, session = make_paginate_client(pages_payload, path="/supplier.json")

        pages = list(paginate(client, "/supplier.json"))

        self.assertEqual(session.requested_pages(), [1, 2, 3])
        self.assertEqual(len(pages), 3)

    def test_all_items_across_pages_are_yielded_in_order_without_overlap(self) -> None:
        pages_payload = laravel_rows_pages(150, per_page=50, start_id=1)
        client, _ = make_paginate_client(pages_payload, path="/supplier.json")

        ids = [item["id"] for page in paginate(client, "/supplier.json") for item in page.items]

        self.assertEqual(ids, list(range(1, 151)))
        self.assertEqual(len(ids), len(set(ids)), "geen duplicaten toegestaan")

    def test_single_page_stops_after_one_request(self) -> None:
        pages_payload = laravel_rows_pages(3, per_page=50)
        client, session = make_paginate_client(pages_payload, path="/supplier.json")

        pages = list(paginate(client, "/supplier.json"))

        self.assertEqual(session.requested_pages(), [1])
        self.assertEqual(len(pages), 1)

    def test_reported_total_is_readable_from_the_response(self) -> None:
        pages_payload = laravel_rows_pages(150, total=951, per_page=50)
        client, _ = make_paginate_client(pages_payload, path="/supplier.json")

        for page in paginate(client, "/supplier.json"):
            self.assertEqual(page.total, 951)

    def test_empty_result_yields_one_empty_page(self) -> None:
        pages_payload = laravel_rows_pages(0, total=0, per_page=50)
        client, _ = make_paginate_client(pages_payload, path="/supplier.json")

        pages = list(paginate(client, "/supplier.json"))

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].items, [])
        self.assertEqual(pages[0].total, 0)


class TestCallerDoesNotChooseTheForm(unittest.TestCase):
    """Dezelfde functie, dezelfde signatuur, voor beide vormen: de
    aanroeper kiest de vorm niet, dat is een eigenschap van het endpoint."""

    def test_both_forms_yield_page_values_via_the_same_call(self) -> None:
        fractal_client, _ = make_paginate_client(
            fractal_rows_pages(2, per_page=50), path="/customer/all.json"
        )
        laravel_client, _ = make_paginate_client(
            laravel_rows_pages(2, per_page=50), path="/supplier.json"
        )

        for client, path in (
            (fractal_client, "/customer/all.json"),
            (laravel_client, "/supplier.json"),
        ):
            with self.subTest(path=path):
                for page in paginate(client, path):
                    self.assertIsInstance(page, Page)
                    self.assertIsInstance(page.items, list)

    def test_items_are_passed_through_untouched(self) -> None:
        source = {"id": 7, "name": "ongewijzigd"}
        fractal_payload = {1: fractal_page(data=[source], current_page=1, total_pages=1, total=1)}
        laravel_payload = {1: laravel_page(data=[source], current_page=1, last_page=1, total=1)}

        for pages_payload, path in (
            (fractal_payload, "/customer/all.json"),
            (laravel_payload, "/supplier.json"),
        ):
            with self.subTest(path=path):
                client, _ = make_paginate_client(pages_payload, path=path)
                (result,) = paginate(client, path)
                self.assertEqual(result.items, [source])


if __name__ == "__main__":
    unittest.main()
