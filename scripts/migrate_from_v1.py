#!/usr/bin/env python3
"""Copy v1.5.x Postgres data into a fresh v2 SQLite database.

Usage
-----
    python scripts/migrate_from_v1.py \\
        --postgres "postgresql://notifyuser:PASSWORD@host:5432/notifications" \\
        --sqlite   ./data/bingealert.db

Prerequisites
-------------
    pip install psycopg2-binary sqlalchemy alembic
    (Or run inside the v2 container with psycopg2-binary installed ad-hoc.)

What it does
------------
    1. Refuses to overwrite an existing SQLite file -- rename it first if needed.
    2. Runs `alembic upgrade head` to create the v2 baseline schema in the SQLite.
    3. Connects read-only to Postgres, copies every BingeAlert table verbatim
       (preserving primary keys), one transaction per table.
    4. Bumps SQLite's internal autoincrement counter to match max(id) per table
       so future inserts don't collide.
    5. Prints row-count parity per table -- the only thing you should see is
       "OK" lines and a summary.

What it does NOT do
-------------------
    - Modify the Postgres source in any way (read-only).
    - Migrate user-facing config out of `system_config` and into config.json
      (deferred to Phase 4 when the read paths get rewritten).
    - Convert naive timestamps to UTC-aware (deferred; preserves v1 behaviour).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Ensure project root on sys.path so `from app...` works when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.engine import Engine

# Tables in dependency order: parents before children. Anything not listed
# here will be skipped with a warning -- explicit allow-list is safer than
# blind reflection (catches drift / accidental tables).
TABLE_ORDER = [
    "users",
    "media_requests",
    "shared_requests",
    "episode_tracking",
    "notifications",
    "reported_issues",
    "maintenance_windows",
    "system_config",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--postgres", required=True, help="Source Postgres URL (read-only)")
    p.add_argument("--sqlite", required=True, help="Target SQLite path. Must not already exist.")
    p.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per INSERT batch (default 500; reduce if memory-constrained)",
    )
    p.add_argument(
        "--skip-table",
        action="append",
        default=[],
        help="Table name to skip. Repeatable.",
    )
    return p.parse_args()


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run_alembic_upgrade(sqlite_path: Path) -> None:
    """Build the v2 schema in the SQLite by running alembic upgrade head.

    Sets DATA_DIR so app.config.settings.database_url resolves to the same
    SQLite path we're creating. Runs alembic as a subprocess so we don't have
    to re-import config after mutating environment.
    """
    env = os.environ.copy()
    env["DATA_DIR"] = str(sqlite_path.parent)
    # Override the filename in case the user passed a non-default name.
    env["SQLITE_FILENAME"] = sqlite_path.name

    project_root = Path(__file__).resolve().parent.parent
    print(f"[alembic] upgrade head  ->  {sqlite_path}")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        fail("alembic upgrade failed -- see output above")
    print(f"[alembic] schema ready")


def copy_table(src: Engine, dst: Engine, table_name: str, batch_size: int) -> tuple[int, int]:
    """Copy one table from src -> dst. Returns (src_rows, dst_rows_inserted)."""
    src_meta = MetaData()
    src_meta.reflect(bind=src, only=[table_name])
    if table_name not in src_meta.tables:
        print(f"[skip ] {table_name}: not in source DB")
        return (0, 0)
    src_table = src_meta.tables[table_name]

    dst_meta = MetaData()
    dst_meta.reflect(bind=dst, only=[table_name])
    if table_name not in dst_meta.tables:
        fail(f"{table_name} missing from target SQLite -- alembic upgrade may not have run")
    dst_table = dst_meta.tables[table_name]

    # Use the column intersection -- if v1 had columns v2 dropped (or vice versa),
    # we copy what we can and warn on the rest.
    src_cols = {c.name for c in src_table.columns}
    dst_cols = {c.name for c in dst_table.columns}
    shared = [c.name for c in dst_table.columns if c.name in src_cols]
    src_only = src_cols - dst_cols
    dst_only = dst_cols - src_cols
    if src_only:
        print(f"[warn ] {table_name}: source-only columns dropped: {sorted(src_only)}")
    if dst_only:
        print(f"[warn ] {table_name}: target-only columns left NULL/default: {sorted(dst_only)}")

    select_stmt = select(*[src_table.c[c] for c in shared])
    inserted = 0
    with src.connect() as src_conn, dst.begin() as dst_conn:
        result = src_conn.execution_options(stream_results=True).execute(select_stmt)
        batch: list[dict] = []
        for row in result.mappings():
            batch.append(dict(row))
            if len(batch) >= batch_size:
                dst_conn.execute(dst_table.insert(), batch)
                inserted += len(batch)
                batch.clear()
        if batch:
            dst_conn.execute(dst_table.insert(), batch)
            inserted += len(batch)

    from sqlalchemy import func

    with src.connect() as src_conn:
        src_count = src_conn.execute(select(func.count()).select_from(src_table)).scalar_one()
    return (src_count, inserted)


def reset_sqlite_sequences(dst: Engine, tables: list[str]) -> None:
    """SQLite uses sqlite_sequence to remember the last autoincrement value.

    Since we copied rows with their original ids, future inserts (which use
    AUTOINCREMENT) need the counter set to max(id) so we don't collide.
    """
    # sqlite_sequence is only auto-created when a table uses AUTOINCREMENT.
    # Our schema uses plain `INTEGER PRIMARY KEY`, where SQLite's rowid logic
    # picks max(id)+1 on its own -- no manual reset needed. We still try to
    # update the counter when sqlite_sequence DOES exist (some installs may
    # have AUTOINCREMENT in custom migrations), but skip silently otherwise.
    with dst.begin() as conn:
        seq_exists = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
        if not seq_exists:
            return
        # Table names come from our hardcoded TABLE_ORDER, not user input.
        for t in tables:
            row = conn.exec_driver_sql(f"SELECT MAX(id) FROM {t}").fetchone()
            if row is None or row[0] is None:
                continue
            max_id = row[0]
            existing = conn.exec_driver_sql(
                f"SELECT name FROM sqlite_sequence WHERE name = '{t}'"
            ).fetchone()
            if existing is None:
                conn.exec_driver_sql(
                    f"INSERT INTO sqlite_sequence (name, seq) VALUES ('{t}', {max_id})"
                )
            else:
                conn.exec_driver_sql(
                    f"UPDATE sqlite_sequence SET seq = {max_id} WHERE name = '{t}'"
                )


def main() -> int:
    args = parse_args()
    sqlite_path = Path(args.sqlite).resolve()
    if sqlite_path.exists():
        fail(f"target SQLite already exists: {sqlite_path}\n  rename or delete it first.")
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: build empty v2 schema in the target SQLite.
    run_alembic_upgrade(sqlite_path)

    src_engine = create_engine(args.postgres)
    dst_engine = create_engine(f"sqlite:///{sqlite_path}", future=True)

    print("\nCopying tables (dependency order):")
    summary: list[tuple[str, int, int]] = []
    for table_name in TABLE_ORDER:
        if table_name in args.skip_table:
            print(f"[skip ] {table_name}: --skip-table requested")
            continue
        src_count, dst_count = copy_table(src_engine, dst_engine, table_name, args.batch_size)
        ok = "OK   " if src_count == dst_count else "MISMATCH"
        print(f"[{ok}] {table_name}: {src_count} -> {dst_count}")
        summary.append((table_name, src_count, dst_count))

    print("\nResetting SQLite autoincrement counters...")
    reset_sqlite_sequences(dst_engine, [t for t, s, d in summary if d > 0])

    print("\nSummary:")
    total_src = sum(s for _, s, _ in summary)
    total_dst = sum(d for _, _, d in summary)
    for t, s, d in summary:
        print(f"  {t:24s}  src={s:>8}  dst={d:>8}")
    print(f"  {'TOTAL':24s}  src={total_src:>8}  dst={total_dst:>8}")

    if total_src != total_dst:
        fail("row-count mismatch -- review warnings above before using this DB")
    print("\nDone. Review the output, then point your v2 container at this SQLite file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
