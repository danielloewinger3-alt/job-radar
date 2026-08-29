"""Explicit, deterministic registry of the future feature domains.

This module is the single source of truth for which domain packages the
application wires in at startup, and in what order. It performs **no** globbing
or dynamic directory scanning: every module path below is written out literally.

It imports nothing from ``backend`` and opens no database connection, so it is
safe to import from anywhere -- including ``backend.db.init_db`` and
``backend.main`` -- without a circular import or an import-time DB access.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter


@dataclass(frozen=True)
class Feature:
    """One future workstream's wiring.

    ``models_module`` / ``migrate_module`` are ``None`` for a domain that does
    not own tables or a migration -- e.g. ``integrations``, which is a
    router-only shared surface.
    """

    name: str
    router_module: str
    models_module: str | None = None
    migrate_module: str | None = None


# Order is deterministic and load-bearing: model imports (phase 1), feature
# migrations (phase 4) and router registration all iterate this tuple in order.
FEATURES: tuple[Feature, ...] = (
    Feature(
        name="tracker",
        router_module="backend.tracker.router",
        models_module="backend.tracker.models",
        migrate_module="backend.tracker.migrate",
    ),
    Feature(
        name="projectfiles",
        router_module="backend.projectfiles.router",
        models_module="backend.projectfiles.models",
        migrate_module="backend.projectfiles.migrate",
    ),
    Feature(
        name="assist",
        router_module="backend.assist.router",
        models_module="backend.assist.models",
        migrate_module="backend.assist.migrate",
    ),
    Feature(
        name="outreach",
        router_module="backend.outreach.router",
        models_module="backend.outreach.models",
        migrate_module="backend.outreach.migrate",
    ),
    Feature(
        name="integrations",
        router_module="backend.integrations.router",
        models_module=None,
        migrate_module=None,
    ),
)


def import_feature_models() -> None:
    """Phase 1: import each domain's ``models`` module so any SQLModel tables it
    declares are registered on ``SQLModel.metadata`` before ``create_all()``.

    Every ``models`` module is currently empty, so this registers nothing yet.
    """
    for feature in FEATURES:
        if feature.models_module is not None:
            importlib.import_module(feature.models_module)


def run_feature_migrations(engine) -> None:
    """Phase 4: run each registered feature migration exactly once, in
    ``FEATURES`` order, after the legacy additive migration.

    Every ``migrate.run`` is currently a no-op.
    """
    for feature in FEATURES:
        if feature.migrate_module is not None:
            module = importlib.import_module(feature.migrate_module)
            module.run(engine)


def feature_routers() -> list[tuple[str, APIRouter]]:
    """Return ``(name, router)`` for every feature, in ``FEATURES`` order, for
    ``backend.main`` to mount. Importing a router module pulls in only
    ``fastapi`` -- no database, no ``backend.main``.
    """
    routers: list[tuple[str, APIRouter]] = []
    for feature in FEATURES:
        module = importlib.import_module(feature.router_module)
        routers.append((feature.name, module.router))
    return routers
