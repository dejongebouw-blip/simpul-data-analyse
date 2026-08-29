"""Bron-JSON naar rijen voor de drie entiteiten: ``customer``, ``project``,
``supplier``.

Het veldcontract hieronder is letterlijk overgetypt uit
``inventaris-endpoints.md`` en uit issue 06 — niet afgeleid, niet verzonnen.
``tests/test_field_contract.py`` typt dezelfde lijsten nóg een keer over en
vergelijkt ze, zodat een stille hernoeming hier zichtbaar wordt in de diff van
de test.

Elke ``parse_*_list`` faalt hard (``MissingFieldError``) zodra een verwacht
bronveld ontbreekt. Er wordt nergens ``.get()`` gebruikt met ``None`` als
stille uitkomst: een ontbrekend veld levert een rij op die nooit geschreven
wordt, niet een rij met een NULL die niemand opmerkt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from bs4 import BeautifulSoup

from simpul_extract.observability import get_logger

logger = get_logger(__name__)


class MissingFieldError(Exception):
    """Geworpen wanneer een bronrecord een verwacht veld mist."""


# ------------------------------------------------------------------ customer
# Bron: GET /customer/all.json (951 rijen, Fractal-paginering, 50/pagina).
CUSTOMER_LIST_FIELDS = (
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


def parse_customer_list(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Zet elk record uit ``/customer/all.json`` om naar een rij.

    ``email`` staat niet in deze bron-JSON (alleen op de detailpagina) en is
    daarom het enige veld dat hier bewust ``None`` is; issue 09 vult het
    vanuit ``/customer/{id}``.
    """
    rows = []
    for record in records:
        row = _require_fields(record, CUSTOMER_LIST_FIELDS, entity="customer")
        row["email"] = None
        rows.append(row)
    return rows


# --------------------------------------------------------------- customer detail
# Bron: GET /customer/{id} (HTML). Levert alleen `email`; contactpersonen,
# notities, bestanden en de projectlijst op dat scherm zijn buiten scope
# (issue 09 — Out of Scope in het PRD).


@dataclass(frozen=True)
class Detail:
    """Resultaat van het parsen van één relatiedetailpagina. `email` is
    `None` wanneer de pagina geen `mailto:`-anker draagt — dat is een
    toegestane, geen foutieve, uitkomst."""

    email: Optional[str]


def parse_customer_detail(html: str) -> Detail:
    """Haalt het e-mailadres uit het eerste `a[href^="mailto:"]`-anker in
    documentvolgorde.

    Structureel, niet regex op vrije tekst: een adres dat ergens in een
    notitieveld staat maar niet in een `mailto:`-anker wordt genegeerd, ook
    al zou een regex het daar wél vinden.
    """
    soup = BeautifulSoup(html, "html.parser")
    anchor = soup.select_one('a[href^="mailto:"]')
    if anchor is None:
        return Detail(email=None)

    href = anchor.get("href", "")
    email = href[len("mailto:"):].split("?", 1)[0].strip()
    return Detail(email=email or None)


def fetch_customer_emails(client: Any, rows: List[Dict[str, Any]]) -> int:
    """Haalt voor elke customer-rij `/customer/{id}` op en vult `email`.

    Serieel via de meegegeven `client` (de HTTP-laag uit issue 03): dezelfde
    pauze en backoff als elk ander verzoek, geen apart HTTP-pad. Muteert
    `rows` in place en retourneert het aantal rijen waarvoor een
    e-mailadres gevonden is, zodat de ronde dat kan melden.
    """
    found = 0
    total = len(rows)
    for number, row in enumerate(rows, start=1):
        response = client.get(f"/customer/{row['id']}")
        detail = parse_customer_detail(response.text)
        row["email"] = detail.email
        if detail.email is not None:
            found += 1
        # Dit is de langste stap van de ronde: eén serieel verzoek per relatie,
        # bij 951 relaties het leeuwendeel van de looptijd. Zonder tussenstand
        # is een ronde die hier hangt niet te onderscheiden van een ronde die
        # gewoon werkt.
        if number % 100 == 0 or number == total:
            logger.info("detailpagina's: %d van %d, %d e-mailadressen", number, total, found)
    return found


# ------------------------------------------------------------------- project
# Bron: GET /project/all.json (2793 rijen, Fractal-paginering, 50/pagina).
PROJECT_FIELDS = (
    "id",
    "project_number",
    "name",
    "status_id",
    "url_show",
    "invoiceable_amount",
    "project_location",
)

# Geneste "customer"-object op elk projectrecord; wordt platgeslagen naar
# customer_<veld>. De bron-JSON draagt hier geen customer.id, dus dit wordt
# niet als aparte relatie/foreign key opgeslagen.
PROJECT_CUSTOMER_FIELDS = (
    "title",
    "address",
    "zipcode",
    "city",
    "phone",
    "mobile",
)


def parse_project_list(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Zet elk record uit ``/project/all.json`` om naar een rij, met het
    geneste ``customer``-object platgeslagen naar ``customer_*`` kolommen."""
    rows = []
    for record in records:
        row = _require_fields(record, PROJECT_FIELDS, entity="project")
        if "customer" not in record:
            raise MissingFieldError(
                "project: bronveld 'customer' ontbreekt in record "
                f"{record!r}"
            )
        nested = record["customer"]
        nested_row = _require_fields(nested, PROJECT_CUSTOMER_FIELDS, entity="project.customer")
        for field, value in nested_row.items():
            row[f"customer_{field}"] = value
        rows.append(row)
    return rows


# ------------------------------------------------------------------ supplier
# Bron: GET /supplier.json (98 rijen, Laravel-paginator, 50/pagina).
SUPPLIER_FIELDS = (
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


def parse_supplier_list(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Zet elk record uit ``/supplier.json`` om naar een rij."""
    return [
        _require_fields(record, SUPPLIER_FIELDS, entity="supplier")
        for record in records
    ]


def _require_fields(record: Mapping[str, Any], fields: Iterable[str], *, entity: str) -> Dict[str, Any]:
    """Kopieert exact ``fields`` uit ``record`` naar een nieuwe rij.

    Werpt ``MissingFieldError`` zodra een veld ontbreekt, met de entiteitsnaam
    en het ontbrekende veld erin — geen ``.get()``, geen stille ``None``.
    """
    row = {}
    for field in fields:
        if field not in record:
            raise MissingFieldError(
                f"{entity}: verwacht bronveld '{field}' ontbreekt in record {record!r}"
            )
        row[field] = record[field]
    return row
