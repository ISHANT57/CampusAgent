"""Import every model here so Alembic's autogenerate sees them.

Alembic compares Base.metadata against the live database. A model that is
never imported is absent from that metadata, so autogenerate silently emits an
empty migration and the table is never created — a failure with no error
message. This module is the single place that guarantees registration.
"""

from app.models.run import Run, RunStatus  # noqa: F401
from app.models.step import Step, StepKind  # noqa: F401

__all__ = ["Run", "RunStatus", "Step", "StepKind"]
