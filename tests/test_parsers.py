"""Toetst de drie entiteitsparsers (issue 06, SC-3/US-3): elke `parse_*_list`
mapt bron-JSON naar rijen op de exacte veldnamen uit de inventaris, en faalt
hard zodra een verwacht veld ontbreekt — er wordt nooit een rij met een
stille `None` geschreven.

Alle fixtures komen uit `tests._stubs`, opgebouwd op de bronveldnamen zelf
(niet op de kolomnamen), conform
`lessons/2026-08-29-fixtures-uit-de-bron-niet-uit-het-hoofd.md`.
"""
from __future__ import annotations

import unittest

from simpul_extract.parsers import (
    MissingFieldError,
    parse_customer_list,
    parse_project_list,
    parse_supplier_list,
)
from tests._stubs import (
    synthetic_customer_record,
    synthetic_project_record,
    synthetic_supplier_record,
)


class TestParseCustomerList(unittest.TestCase):
    def test_maps_every_expected_field_through_unchanged(self) -> None:
        record = synthetic_customer_record()

        (row,) = parse_customer_list([record])

        for field in (
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
        ):
            self.assertEqual(row[field], record[field])

    def test_email_is_none_because_the_list_json_does_not_carry_it(self) -> None:
        (row,) = parse_customer_list([synthetic_customer_record()])

        self.assertIsNone(row["email"])

    def test_missing_field_raises_instead_of_writing_a_silent_none(self) -> None:
        record = synthetic_customer_record()
        del record["title"]

        with self.assertRaises(MissingFieldError) as ctx:
            parse_customer_list([record])

        self.assertIn("customer", str(ctx.exception))
        self.assertIn("title", str(ctx.exception))

    def test_missing_invoiceable_style_field_is_not_silently_dropped(self) -> None:
        """Tegen-pin op de vorige poging: die sloeg `display_status` /
        `tasks_status` gewoon over in plaats van te falen."""
        record = synthetic_customer_record()
        del record["tasks_status"]

        with self.assertRaises(MissingFieldError):
            parse_customer_list([record])

    def test_renaming_title_to_name_makes_the_parser_fail(self) -> None:
        """Verplichte tegen-pin uit issue 06: de vorige poging vroeg `name`
        waar de bron `title` levert en bleef groen. Een fixture die dat
        nabootst moet hier rood worden."""
        record = synthetic_customer_record()
        record["name"] = record.pop("title")

        with self.assertRaises(MissingFieldError) as ctx:
            parse_customer_list([record])

        self.assertIn("title", str(ctx.exception))

    def test_renaming_name_back_to_title_makes_the_parser_pass_again(self) -> None:
        """Andere kant van dezelfde tegen-pin: hernoemen naar de echte
        bronnaam maakt de parser weer groen."""
        record = synthetic_customer_record()
        record["name"] = record.pop("title")
        record["title"] = record.pop("name")

        (row,) = parse_customer_list([record])

        self.assertEqual(row["title"], synthetic_customer_record()["title"])


class TestParseProjectList(unittest.TestCase):
    def test_maps_top_level_fields_through_unchanged(self) -> None:
        record = synthetic_project_record()

        (row,) = parse_project_list([record])

        for field in (
            "id",
            "project_number",
            "name",
            "status_id",
            "url_show",
            "project_location",
        ):
            self.assertEqual(row[field], record[field])

    def test_flattens_nested_customer_object_to_prefixed_columns(self) -> None:
        record = synthetic_project_record()

        (row,) = parse_project_list([record])

        self.assertEqual(row["customer_title"], record["customer"]["title"])
        self.assertEqual(row["customer_address"], record["customer"]["address"])
        self.assertEqual(row["customer_zipcode"], record["customer"]["zipcode"])
        self.assertEqual(row["customer_city"], record["customer"]["city"])
        self.assertEqual(row["customer_phone"], record["customer"]["phone"])
        self.assertEqual(row["customer_mobile"], record["customer"]["mobile"])

    def test_nested_customer_object_is_not_kept_as_its_own_relation(self) -> None:
        (row,) = parse_project_list([synthetic_project_record()])

        self.assertNotIn("customer", row)
        self.assertNotIn("customer_id", row)

    def test_missing_status_id_raises_instead_of_being_dropped(self) -> None:
        """Tegen-pin: een bronveld dat wegvalt moet de ronde laten vallen, niet
        stilzwijgend een lege kolom opleveren. Stond eerst op
        `invoiceable_amount`; dat veld is vervallen omdat de bron het nooit
        vult en de factuurmodule 403 geeft."""
        record = synthetic_project_record()
        del record["status_id"]

        with self.assertRaises(MissingFieldError) as ctx:
            parse_project_list([record])

        self.assertIn("status_id", str(ctx.exception))

    def test_status_id_wordt_als_label_doorgegeven_niet_als_getal(self) -> None:
        """De bron levert 2793 van 2793 als tekst ('Archief', 'Gesloten', ...).
        Een parser die hier een int van maakt, breekt op de echte data."""
        record = synthetic_project_record(status_id="Archief")

        (row,) = parse_project_list([record])

        self.assertEqual(row["status_id"], "Archief")

    def test_renaming_project_number_to_code_makes_the_parser_fail(self) -> None:
        """Tegen-pin: de vorige poging vroeg `code` waar de bron
        `project_number` levert."""
        record = synthetic_project_record()
        record["code"] = record.pop("project_number")

        with self.assertRaises(MissingFieldError):
            parse_project_list([record])

    def test_missing_nested_customer_field_raises(self) -> None:
        record = synthetic_project_record(drop_customer_fields=["phone"])

        with self.assertRaises(MissingFieldError) as ctx:
            parse_project_list([record])

        self.assertIn("customer", str(ctx.exception))
        self.assertIn("phone", str(ctx.exception))

    def test_missing_customer_object_entirely_raises(self) -> None:
        record = synthetic_project_record()
        del record["customer"]

        with self.assertRaises(MissingFieldError) as ctx:
            parse_project_list([record])

        self.assertIn("customer", str(ctx.exception))


class TestParseSupplierList(unittest.TestCase):
    def test_maps_every_expected_field_through_unchanged(self) -> None:
        record = synthetic_supplier_record()

        (row,) = parse_supplier_list([record])

        for field in (
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
        ):
            self.assertEqual(row[field], record[field])

    def test_missing_field_raises_instead_of_writing_a_silent_none(self) -> None:
        record = synthetic_supplier_record()
        del record["email"]

        with self.assertRaises(MissingFieldError) as ctx:
            parse_supplier_list([record])

        self.assertIn("supplier", str(ctx.exception))
        self.assertIn("email", str(ctx.exception))

    def test_missing_text_field_raises(self) -> None:
        """Tegen-pin: de vorige poging vroeg `text` op relaties waar dat een
        leveranciersveld is; hier hoort het juist verplicht te zijn."""
        record = synthetic_supplier_record()
        del record["text"]

        with self.assertRaises(MissingFieldError):
            parse_supplier_list([record])


class TestParsersProcessMultipleRecords(unittest.TestCase):
    def test_customer_list_returns_one_row_per_record_in_order(self) -> None:
        records = [
            synthetic_customer_record(id=1),
            synthetic_customer_record(id=2),
            synthetic_customer_record(id=3),
        ]

        rows = parse_customer_list(records)

        self.assertEqual([row["id"] for row in rows], [1, 2, 3])

    def test_a_single_bad_record_fails_the_whole_batch(self) -> None:
        """Geen halve schrijfactie: één ontbrekend veld ergens in de batch
        stopt de hele parse, niet alleen dat ene record."""
        good = synthetic_customer_record(id=1)
        bad = synthetic_customer_record(id=2)
        del bad["city"]

        with self.assertRaises(MissingFieldError):
            parse_customer_list([good, bad])


if __name__ == "__main__":
    unittest.main()
