"""Assist / Alfred schema migration (skeleton).

``run(engine)`` is the deterministic hook ``backend.features.run_feature_migrations``
calls after the legacy additive migration. It performs no work this sprint.
"""


def run(engine) -> None:
    """No-op until the assist workstream needs a migration."""
    return None
