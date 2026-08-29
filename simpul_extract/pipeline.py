"""De verticale doorsnede: pagineren, parsen, wegschrijven, verantwoorden.

Per entiteit doorloopt :func:`extract_entity` de bron paginagewijs, geeft
elke rij een ``fetched_at`` en schrijft via de schrijfnaad
(``upsert(table, rows)``, zie :mod:`simpul_extract.storage`). Na afloop landt
per run en per entiteit één auditregel in ``extraction_run`` met tijdstip,
opgeslagen rijen en het door de bron gemelde totaal.

Daarna volgt de volledigheidscontrole (SC-2): wijkt het aantal opgeslagen
rijen af van het gemelde ``total``, dan faalt de run met een niet-nul exit
code en een melding die entiteit, verwacht en gevonden noemt. De auditregel
wordt óók bij een tekort geschreven, zodat het spoor de mislukte run toont.
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, TextIO

from simpul_extract.customers import (
    CUSTOMER_PATH,
    CUSTOMER_TABLE,
    parse_customer_list,
)
from simpul_extract.paginate import Page, fractal_pages, laravel_pages
from simpul_extract.projects import (
    PROJECT_PATH,
    PROJECT_TABLE,
    parse_project_list,
)
from simpul_extract.suppliers import (
    SUPPLIER_PATH,
    SUPPLIER_TABLE,
    parse_supplier_list,
)


class CompletenessError(RuntimeError):
    """Het aantal opgeslagen rijen wijkt af van het door de bron gemelde totaal."""

    def __init__(self, *, entity: str, expected: int, found: int) -> None:
        self.entity = entity
        self.expected = expected
        self.found = found
        super().__init__(
            f"volledigheidscontrole gefaald voor entiteit '{entity}': "
            f"bron meldt {expected} rijen, opgeslagen {found}"
        )


@dataclass(frozen=True)
class EntitySpec:
    """Eén te extraheren entiteit: waar hij vandaan komt en waar hij landt."""

    entity: str
    table: str
    path: str
    pages: Callable[[Any, str], Iterator[Page]]
    parse: Callable[[Any], List[Dict[str, Any]]]


SUPPLIER_SPEC = EntitySpec(
    entity="supplier",
    table=SUPPLIER_TABLE,
    path=SUPPLIER_PATH,
    pages=laravel_pages,
    parse=parse_supplier_list,
)

CUSTOMER_SPEC = EntitySpec(
    entity="customer",
    table=CUSTOMER_TABLE,
    path=CUSTOMER_PATH,
    pages=fractal_pages,
    parse=parse_customer_list,
)

PROJECT_SPEC = EntitySpec(
    entity="project",
    table=PROJECT_TABLE,
    path=PROJECT_PATH,
    pages=fractal_pages,
    parse=parse_project_list,
)

DEFAULT_SPECS = (CUSTOMER_SPEC, PROJECT_SPEC, SUPPLIER_SPEC)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def extract_entity(
    *,
    client: Any,
    writer: Any,
    spec: EntitySpec,
    run_id: str,
    now: Callable[[], datetime] = _utcnow,
) -> Dict[str, Any]:
    started_at = now()
    rows_stored = 0
    source_total: Optional[int] = None
    for page in spec.pages(client, spec.path):
        rows = spec.parse(page.items)
        fetched_at = now().isoformat()
        for row in rows:
            row["fetched_at"] = fetched_at
        writer.upsert(spec.table, rows)
        rows_stored += len(rows)
        if page.total is not None:
            source_total = page.total
    audit_row = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": now().isoformat(),
        "entity": spec.entity,
        "rows_stored": rows_stored,
        "source_total": source_total,
    }
    writer.upsert("extraction_run", [audit_row])
    if source_total is not None and rows_stored != source_total:
        raise CompletenessError(
            entity=spec.entity, expected=source_total, found=rows_stored
        )
    return audit_row


def run(
    *,
    client: Any,
    writer: Any,
    specs: Sequence[EntitySpec] = DEFAULT_SPECS,
    run_id: Optional[str] = None,
    now: Callable[[], datetime] = _utcnow,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    if run_id is None:
        run_id = str(uuid.uuid4())
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    exit_code = 0
    reports = []
    for spec in specs:
        try:
            audit = extract_entity(
                client=client, writer=writer, spec=spec, run_id=run_id, now=now
            )
        except CompletenessError as exc:
            stderr.write(f"error: {exc}\n")
            exit_code = 1
            reports.append((spec.entity, exc.found, exc.expected))
        else:
            reports.append(
                (spec.entity, audit["rows_stored"], audit["source_total"])
            )
    for entity, stored, total in reports:
        reported = "onbekend" if total is None else str(total)
        stdout.write(f"{entity}: {stored} opgeslagen van {reported} gemeld\n")
    return exit_code
