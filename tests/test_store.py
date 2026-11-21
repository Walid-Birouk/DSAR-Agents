"""Writing the world to disk: one database per system, truth kept apart, read-only access."""

import sqlite3
from pathlib import Path

import pytest

from article15.company.models import World
from article15.company.store import TABLES, DataPaths, load_cases, load_owners, load_truths, read_only
from article15.schemas import System


def test_round_trip(world: World, data: DataPaths) -> None:
    assert load_cases(data) == world.cases
    assert load_truths(data) == world.truths
    assert load_owners(data) == world.owners
    for system, tables in TABLES.items():
        with read_only(data.db(system)) as con:
            for table in tables:
                assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == len(
                    getattr(world, table)
                )


def test_system_databases_hold_no_ground_truth(data: DataPaths) -> None:
    for system in System:
        with read_only(data.db(system)) as con:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert names == set(TABLES[system])
    assert (data.truth_dir / "truths.jsonl").exists()
    assert not any(p.suffix == ".sqlite" for p in data.truth_dir.iterdir())


def test_read_only_connections_cannot_write(data: DataPaths) -> None:
    with read_only(data.db(System.CRM)) as con, pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM customers")


def test_missing_database_says_how_to_create_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="a15 generate"), read_only(tmp_path / "crm.sqlite"):
        pass
