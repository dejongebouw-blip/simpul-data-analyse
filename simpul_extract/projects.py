"""Projecten: van bron-JSON naar rijen voor de tabel ``project``.

De veldnamen komen 1-op-1 uit ``inventaris-endpoints.md`` en de kolomnamen
uit ``schema-postgres.sql``. Het genest meegeleverde relatie-object wordt
genormaliseerd op kolommen met voorvoegsel ``customer_``; er wordt géén
koppeling naar ``customer.id`` verzonnen, want die bestaat niet in de
bron-JSON.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Union

PROJECT_PATH = "/project/all.json"
PROJECT_TABLE = "project"
PROJECT_SCALAR_COLUMNS = (
    "id",
    "name",
    "code",
    "status",
    "url_show",
)
PROJECT_CUSTOMER_FIELDS = (
    "name",
    "address",
    "zipcode",
    "city",
)
PROJECT_COLUMNS = PROJECT_SCALAR_COLUMNS + tuple(
    f"customer_{field}" for field in PROJECT_CUSTOMER_FIELDS
)


def parse_project_list(
    json: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]]
) -> List[Dict[str, Any]]:
    """Accepteert een volledige paginapayload of een kale itemlijst."""
    if isinstance(json, Mapping):
        items = json.get("data") or []
    else:
        items = json
    rows: List[Dict[str, Any]] = []
    for item in items:
        row = {column: item.get(column) for column in PROJECT_SCALAR_COLUMNS}
        nested = item.get("customer") or {}
        for field in PROJECT_CUSTOMER_FIELDS:
            row[f"customer_{field}"] = nested.get(field)
        rows.append(row)
    return rows
