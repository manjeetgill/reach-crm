"""A fresh install must be upgradeable through every schema revision."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1]


def test_fresh_sqlite_upgrade_reaches_head(tmp_path):
    database = tmp_path / "fresh.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database}"

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    check = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=API_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check.returncode == 0, check.stderr

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")}
    assert {"organizations", "contacts", "knowledge_documents", "campaigns", "surveys", "outbound_jobs"} <= tables
