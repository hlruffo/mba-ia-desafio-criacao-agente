from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .repository import Repository

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
database = Path(os.getenv("AURORA_DATABASE", str(ROOT / "data" / "aurora.sqlite3"))).resolve()
if database.exists():
    database.unlink()
for suffix in ("-wal", "-shm"):
    sidecar = Path(str(database) + suffix)
    if sidecar.exists():
        sidecar.unlink()
repository = Repository(database, ROOT / "dados")
repository.restore()
print("Dados iniciais restaurados; sessões também foram removidas.")
