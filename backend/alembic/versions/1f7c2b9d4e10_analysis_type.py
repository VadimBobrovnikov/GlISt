"""analysis type for scenarios

Revision ID: 1f7c2b9d4e10
Revises: 92fc2ea5d12d
Create Date: 2026-11-15 16:05:00.000000

Добавляет явный тип анализа (сценарий) в запросы и запуски. Старые записи
получают значение ``vegetation_change`` и продолжают считаться как прежде.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f7c2b9d4e10"
down_revision: str | Sequence[str] | None = "92fc2ea5d12d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLOWED = "('vegetation_change', 'built_up_change', 'flood_extent')"


def upgrade() -> None:
    """Upgrade schema."""
    for table, constraint in (
        ("query", "query_analysis_type_check"),
        ("analysis_run", "analysis_run_analysis_type_check"),
    ):
        op.add_column(
            table,
            sa.Column(
                "analysis_type",
                sa.String(length=32),
                nullable=False,
                server_default="vegetation_change",
            ),
        )
        op.create_check_constraint(
            constraint,
            table,
            f"analysis_type IN {_ALLOWED}",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table, constraint in (
        ("query", "query_analysis_type_check"),
        ("analysis_run", "analysis_run_analysis_type_check"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.drop_column(table, "analysis_type")
