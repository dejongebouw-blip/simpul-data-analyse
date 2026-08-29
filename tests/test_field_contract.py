"""Toetst dat het veldcontract in `simpul_extract/parsers.py` letterlijk
gelijk is aan de tabel in issue 06 (issue 06, SC-3).

Dit is expres redundant met de literals in `parsers.py`: de lijsten hieronder
zijn nóg een keer met de hand overgetypt uit het issue, onafhankelijk van de
code. Een stille hernoeming in de parser (zoals `title` -> `name` in de vorige
poging, zie `lessons/2026-08-29-fixtures-uit-de-bron-niet-uit-het-hoofd.md`)
maakt deze test rood, ook al zou de parser zelf intern consistent blijven.
"""
from __future__ import annotations

import unittest

from simpul_extract.parsers import (
    CUSTOMER_LIST_FIELDS,
    PROJECT_CUSTOMER_FIELDS,
    PROJECT_FIELDS,
    SUPPLIER_FIELDS,
)


class TestCustomerFieldContract(unittest.TestCase):
    def test_matches_issue_table_literally(self) -> None:
        expected = (
            "id",
            "customer_number",
            "title",
            "address",
            "zipcode",
            "city",
            "phone",
            "mobile",
            "display_status",
            "tasks_status",
            "url_show",
        )
        self.assertEqual(CUSTOMER_LIST_FIELDS, expected)


class TestProjectFieldContract(unittest.TestCase):
    def test_top_level_matches_issue_table_literally(self) -> None:
        expected = (
            "id",
            "project_number",
            "name",
            "status_id",
            "url_show",
            "project_location",
        )
        self.assertEqual(PROJECT_FIELDS, expected)

    def test_nested_customer_matches_issue_table_literally(self) -> None:
        expected = (
            "title",
            "address",
            "zipcode",
            "city",
            "phone",
            "mobile",
        )
        self.assertEqual(PROJECT_CUSTOMER_FIELDS, expected)


class TestSupplierFieldContract(unittest.TestCase):
    def test_matches_issue_table_literally(self) -> None:
        expected = (
            "id",
            "name",
            "zipcode",
            "city",
            "email",
            "phone",
            "mobile",
            "address",
            "url_show",
            "text",
        )
        self.assertEqual(SUPPLIER_FIELDS, expected)


if __name__ == "__main__":
    unittest.main()
