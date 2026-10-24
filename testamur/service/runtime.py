"""What a service process holds: the store (two connections: the owner's and the application role's), the engine,
and the investigation workflow with its shared checkpoints."""
from __future__ import annotations

import threading

from .. import blobs, migrations
from ..engine import Engine
from ..modeltier import from_spec
from ..pack import load_pack
from ..store import Store
from .settings import Settings


class SchemaBehind(RuntimeError):
    """The database is not at the schema this code expects: run `testamur db upgrade` first."""


class Runtime:
    def __init__(self, settings: Settings, engine: Engine | None = None, investigations=None,
                 check_schema: bool = True):
        self.settings = settings
        if engine is None:
            if check_schema and migrations.current(settings.database_url) != migrations.head():
                raise SchemaBehind(f"the database is at {migrations.current(settings.database_url)}, the code at "
                                   f"{migrations.head()}: run `testamur db upgrade`")
            store = Store(settings.database_url, tenant_url=settings.tenant_database_url, create_schema=False)
            engine = Engine(store, load_pack(), settings.as_of, settings.blob_root,
                            model=from_spec(settings.model, settings.cassette),
                            blobs=blobs.from_env(settings.blob_root))
        self.engine, self.store = engine, engine.store
        self._investigations = investigations
        self._closables: list = []
        self._lock = threading.Lock()

    @property
    def investigations(self):
        """Built on first use: the API process needs it only to record a person's decision on a run."""
        with self._lock:
            if self._investigations is None:
                from ..investigate import Investigations, checkpointer
                saver, closable = checkpointer(self.settings.checkpoints)
                self._closables.append(closable)
                self._investigations = Investigations(self.engine, self.engine.model, saver)
            return self._investigations

    def close(self) -> None:
        for c in self._closables:
            c.close()
        self.store.engine.dispose()
        if self.store.tenant_engine is not self.store.engine:
            self.store.tenant_engine.dispose()
