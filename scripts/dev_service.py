"""Run the service on one machine without Docker: SQLite, local files, and a worker thread in the same process. For
trying the review page; docker-compose.yml is the deployment-shaped stack.

    python scripts/dev_service.py [--port 8765] [--fresh]

The first run (or --fresh) migrates a new database, loads the synthetic world through the intake, reads it, and
writes fresh demo keys to data/dev-service/keys.txt (synthetic data, local keys)."""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data" / "dev-service"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--fresh", action="store_true", help="start from an empty database")
    a = ap.parse_args()
    if a.fresh and DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True, exist_ok=True)
    env = {"TESTAMUR_DATABASE_URL": f"sqlite:///{(DATA / 'service.db').as_posix()}",
           "TESTAMUR_BLOB_ROOT": str(DATA / "blobs"), "TESTAMUR_CHECKPOINTS": str(DATA / "checkpoints.sqlite"),
           "TESTAMUR_AS_OF": "2026-10-01", "TESTAMUR_SECURE_COOKIES": "0",
           "TESTAMUR_SESSION_SECRET": secrets.token_urlsafe(32)}
    for k, v in env.items():
        os.environ.setdefault(k, v)

    import uvicorn

    from testamur import migrations
    from testamur.service import Runtime, Settings, create_app
    from testamur.service.demo import seed
    from testamur.worker import work

    first = not (DATA / "service.db").exists()
    migrations.upgrade(os.environ["TESTAMUR_DATABASE_URL"])
    rt = Runtime(Settings.from_env())
    if first:
        result = seed(rt, DATA / "world")
        (DATA / "keys.txt").write_text("".join(f"{k['role']} key for firm {k['tenant']} ({name}): {k['key']}\n"
                                               for name, k in result["keys"].items()), encoding="utf-8")
        print(f"demo keys written to {DATA / 'keys.txt'}")
    stop = threading.Event()
    worker = threading.Thread(target=work, args=(rt.engine, "dev-worker"), daemon=True,
                              kwargs={"investigations": rt.investigations, "forever": True, "stop": stop})
    worker.start()
    try:
        uvicorn.run(create_app(runtime=rt), host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        stop.set()
        worker.join(timeout=10)
        rt.close()


if __name__ == "__main__":
    main()
