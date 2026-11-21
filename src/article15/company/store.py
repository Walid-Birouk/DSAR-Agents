"""Write the generated world to disk and open it again.

Layout of a data directory (default `data/generated/`, git-ignored):

    crm.sqlite, orders.sqlite, helpdesk.sqlite, weblogs.sqlite   one database per company system
    cases.jsonl                                                  the requests the desk receives
    truth/truths.jsonl, truth/people.jsonl, truth/owners.jsonl   the answers, for the evals only
    manifest.json                                                seed, generator version, digest, counts

No MCP server opens `truth/`. System databases are opened read-only by everything except this writer.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from article15.company.generator import world_digest
from article15.company.models import (
    CaseTruth,
    Consent,
    Customer,
    Order,
    OrderLine,
    Person,
    PriorRequest,
    RecordOwner,
    RequestCase,
    Ticket,
    TicketMessage,
    WebEvent,
    World,
)
from article15.schemas import System

DEFAULT_DATA_DIR = Path("data/generated")

# system -> table -> (row model, primary key, indexed columns)
TABLES: dict[System, dict[str, tuple[type[BaseModel], str, tuple[str, ...]]]] = {
    System.CRM: {
        "customers": (Customer, "customer_id", ("email",)),
        "consents": (Consent, "consent_id", ("customer_id",)),
        "prior_requests": (PriorRequest, "request_id", ("customer_id",)),
    },
    System.ORDERS: {
        "orders": (Order, "order_id", ("customer_id", "guest_email")),
        "order_lines": (OrderLine, "line_id", ("order_id",)),
    },
    System.HELPDESK: {
        "tickets": (Ticket, "ticket_id", ("customer_id", "requester_email")),
        "messages": (TicketMessage, "message_id", ("ticket_id",)),
    },
    System.WEBLOGS: {
        "events": (WebEvent, "event_id", ("cookie_id", "account_id")),
    },
}


@dataclass(frozen=True)
class DataPaths:
    root: Path

    def db(self, system: System) -> Path:
        return self.root / f"{system.value}.sqlite"

    @property
    def cases(self) -> Path:
        return self.root / "cases.jsonl"

    @property
    def truth_dir(self) -> Path:
        return self.root / "truth"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"


def _sql_type(annotation: Any) -> str:
    return "INTEGER" if annotation in (int, bool) else "TEXT"


def _write_db(
    path: Path, world: World, tables: dict[str, tuple[type[BaseModel], str, tuple[str, ...]]]
) -> None:
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    try:
        for table, (model, pk, indexes) in tables.items():
            fields = model.model_fields
            columns = ", ".join(
                f"{name} {_sql_type(f.annotation)}{' PRIMARY KEY' if name == pk else ''}"
                for name, f in fields.items()
            )
            con.execute(f"CREATE TABLE {table} ({columns})")
            for column in indexes:
                con.execute(f"CREATE INDEX idx_{table}_{column} ON {table} ({column})")
            placeholders = ", ".join("?" for _ in fields)
            rows: list[BaseModel] = getattr(world, table)
            con.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",
                [tuple(row.model_dump(mode="json").values()) for row in rows],
            )
        con.commit()
    finally:
        con.close()


def _write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(row.model_dump_json() + "\n")


def write_world(world: World, root: Path = DEFAULT_DATA_DIR) -> DataPaths:
    paths = DataPaths(root)
    root.mkdir(parents=True, exist_ok=True)
    if paths.truth_dir.exists():
        shutil.rmtree(paths.truth_dir)
    for system, tables in TABLES.items():
        _write_db(paths.db(system), world, tables)
    _write_jsonl(paths.cases, world.cases)
    _write_jsonl(paths.truth_dir / "truths.jsonl", world.truths)
    _write_jsonl(paths.truth_dir / "people.jsonl", world.people)
    _write_jsonl(paths.truth_dir / "owners.jsonl", world.owners)
    manifest = {
        "seed": world.seed,
        "generator_version": world.generator_version,
        "digest": world_digest(world),
        "counts": {
            name: len(getattr(world, name))
            for name in ("customers", "orders", "tickets", "messages", "events", "people", "cases")
        },
    }
    paths.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return paths


def connect_read_only(path: Path) -> sqlite3.Connection:
    """Open a system database so that no statement can change it. The caller closes it."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `a15 generate` first")
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


@contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """`with read_only(path) as con:` opens read-only and always closes (sqlite3's own `with` does not)."""
    con = connect_read_only(path)
    try:
        yield con
    finally:
        con.close()


def _read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    with path.open(encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


def load_cases(paths: DataPaths) -> list[RequestCase]:
    return _read_jsonl(paths.cases, RequestCase)


def load_truths(paths: DataPaths) -> list[CaseTruth]:
    return _read_jsonl(paths.truth_dir / "truths.jsonl", CaseTruth)


def load_people(paths: DataPaths) -> list[Person]:
    return _read_jsonl(paths.truth_dir / "people.jsonl", Person)


def load_owners(paths: DataPaths) -> list[RecordOwner]:
    return _read_jsonl(paths.truth_dir / "owners.jsonl", RecordOwner)
