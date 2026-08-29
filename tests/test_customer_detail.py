"""Toetst het e-mailadres uit de relatiedetailpagina (issue 09, SC-5/US-5):
`parse_customer_detail` haalt het adres structureel uit het eerste
`a[href^="mailto:"]`-anker, nooit uit een regex op vrije tekst, en de
detaillus haalt `/customer/{id}` serieel op via dezelfde HTTP-laag (issue
03) als elk ander verzoek.

Drie synthetische fixtures zijn afgeleid van de drie vastgelegde gevallen op
de echte bron (M1, `inventaris-endpoints.md`): relatie 32475/32476 hebben
een `mailto:`-anker, 32477 niet. Een vierde fixture (adres in vrije tekst,
buiten elk anker) is de tegen-pin tegen een regex-implementatie, conform
`lessons/2026-08-29-fixtures-uit-de-bron-niet-uit-het-hoofd.md`.
"""
from __future__ import annotations

import unittest

from simpul_extract.http_client import SimpulHTTPClient
from simpul_extract.parsers import Detail, fetch_customer_emails, parse_customer_detail
from tests._stubs import (
    RecordingSleep,
    StubResponse,
    StubSession,
    customer_detail_html_with_email_in_free_text,
    customer_detail_html_with_mailto,
    customer_detail_html_without_mailto,
    synthetic_customer_record,
)


class TestParseCustomerDetailMailtoAnchor(unittest.TestCase):
    def test_email_is_read_from_the_mailto_anchor(self) -> None:
        html = customer_detail_html_with_mailto(email="relatie32475@voorbeeld.example")

        detail = parse_customer_detail(html)

        self.assertIsInstance(detail, Detail)
        self.assertEqual(detail.email, "relatie32475@voorbeeld.example")

    def test_page_without_mailto_anchor_yields_none(self) -> None:
        """Relatie 32477 in de inventaris: geen mailto-anker, geen fout —
        `None` is een toegestane uitkomst."""
        html = customer_detail_html_without_mailto()

        detail = parse_customer_detail(html)

        self.assertIsNone(detail.email)

    def test_first_of_multiple_mailto_anchors_wins(self) -> None:
        html = customer_detail_html_with_mailto(
            email="eerste@voorbeeld.example",
            extra_mailtos=["tweede@voorbeeld.example", "derde@voorbeeld.example"],
        )

        detail = parse_customer_detail(html)

        self.assertEqual(detail.email, "eerste@voorbeeld.example")

    def test_email_in_free_text_outside_any_anchor_is_ignored(self) -> None:
        """De tegen-pin op een regex-implementatie: een adres in een
        notitietekst, buiten elk anker, mag niet worden opgepikt. Een regex
        op vrije tekst zou hier `boekhouder@extern-kantoor.example` vinden;
        de structurele haak moet `None` opleveren."""
        html = customer_detail_html_with_email_in_free_text()

        detail = parse_customer_detail(html)

        self.assertIsNone(detail.email)


class TestFetchCustomerEmailsUsesTheHttpLayer(unittest.TestCase):
    def test_email_is_written_onto_each_row(self) -> None:
        rows = [
            synthetic_customer_record(id=32475),
            synthetic_customer_record(id=32476),
            synthetic_customer_record(id=32477),
        ]
        session = StubSession(responses=[
            StubResponse(200, body=customer_detail_html_with_mailto(email="a@voorbeeld.example")),
            StubResponse(200, body=customer_detail_html_with_mailto(email="b@voorbeeld.example")),
            StubResponse(200, body=customer_detail_html_without_mailto()),
        ])
        client = SimpulHTTPClient(session, delay=0.0, sleep=lambda seconds: None)

        found = fetch_customer_emails(client, rows)

        self.assertEqual(rows[0]["email"], "a@voorbeeld.example")
        self.assertEqual(rows[1]["email"], "b@voorbeeld.example")
        self.assertIsNone(rows[2]["email"])
        self.assertEqual(found, 2, "de ronde moet melden bij hoeveel relaties een adres gevonden is")

    def test_requests_go_to_customer_id_paths_in_order(self) -> None:
        rows = [
            synthetic_customer_record(id=32475),
            synthetic_customer_record(id=32476),
        ]
        session = StubSession(responses=[
            StubResponse(200, body=customer_detail_html_without_mailto()),
            StubResponse(200, body=customer_detail_html_without_mailto()),
        ])
        client = SimpulHTTPClient(session, delay=0.0, sleep=lambda seconds: None)

        fetch_customer_emails(client, rows)

        self.assertEqual([c["url"] for c in session.calls], ["/customer/32475", "/customer/32476"])

    def test_detail_fetches_are_serial_with_the_same_pause_and_backoff(self) -> None:
        """Geen apart HTTP-pad: dezelfde SimpulHTTPClient, dus dezelfde
        pauze tussen verzoeken als elk ander pad (issue 03)."""
        rows = [
            synthetic_customer_record(id=32475),
            synthetic_customer_record(id=32476),
            synthetic_customer_record(id=32477),
        ]
        session = StubSession(responses=[
            StubResponse(200, body=customer_detail_html_without_mailto()),
            StubResponse(200, body=customer_detail_html_without_mailto()),
            StubResponse(200, body=customer_detail_html_without_mailto()),
        ])
        sleep = RecordingSleep()
        client = SimpulHTTPClient(session, delay=0.3, sleep=sleep)

        fetch_customer_emails(client, rows)

        self.assertEqual(sleep.calls, [0.3, 0.3])

    def test_a_relation_without_an_anchor_does_not_fail_the_round(self) -> None:
        rows = [synthetic_customer_record(id=32477)]
        session = StubSession(responses=[
            StubResponse(200, body=customer_detail_html_without_mailto()),
        ])
        client = SimpulHTTPClient(session, delay=0.0, sleep=lambda seconds: None)

        found = fetch_customer_emails(client, rows)

        self.assertIsNone(rows[0]["email"])
        self.assertEqual(found, 0)


if __name__ == "__main__":
    unittest.main()
