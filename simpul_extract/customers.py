"""Relaties: van bron-JSON naar rijen voor de tabel ``customer``.

De veldnamen komen 1-op-1 uit ``inventaris-endpoints.md`` en de kolomnamen
uit ``schema-postgres.sql``. Wat de bron niet levert, wordt niet verzonnen;
velden die de bron extra meestuurt worden genegeerd.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Union

CUSTOMER_PATH = "/customer/all.json"
CUSTOMER_TABLE = "customer"
CUSTOMER_COLUMNS = (
    "id",
    "name",
    "address",
    "zipcode",
    "city",
    "email",
    "phone",
    "mobile",
    "url_show",
    "text",
)


def parse_customer_list(
    json: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]]
) -> List[Dict[str, Any]]:
    """Accepteert een volledige paginapayload of een kale itemlijst."""
    if isinstance(json, Mapping):
        items = json.get("data") or []
    else:
        items = json
    return [
        {column: item.get(column) for column in CUSTOMER_COLUMNS}
        for item in items
    ]
