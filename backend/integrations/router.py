"""Integrations HTTP routes (skeleton).

Router-only shared surface: no models, no migration. Exposes an empty
``APIRouter`` for ``backend.main`` to mount. No routes are registered yet.
"""

from fastapi import APIRouter

router = APIRouter()
