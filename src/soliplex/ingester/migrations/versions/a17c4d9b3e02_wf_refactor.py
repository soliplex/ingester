"""wf refactor: lease_token, resource_key, resourcelock

Revision ID: a17c4d9b3e02
Revises: fcb86edb6510
Create Date: 2026-04-29 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "a17c4d9b3e02"
down_revision: Union[str, Sequence[str], None] = "fcb86edb6510"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runstep",
        sa.Column(
            "lease_token",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.add_column(
        "runstep",
        sa.Column(
            "resource_key",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_runstep_resource_key",
        "runstep",
        ["resource_key"],
        unique=False,
    )

    op.create_table(
        "resourcelock",
        sa.Column(
            "resource_key",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        ),
        sa.Column(
            "holder_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        ),
        sa.Column(
            "holder_kind",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        ),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column(
            "acquired_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column("holder_meta", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("resource_key"),
    )
    op.create_index(
        "ix_resourcelock_expires_at",
        "resourcelock",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_resourcelock_expires_at", table_name="resourcelock")
    op.drop_table("resourcelock")
    op.drop_index("ix_runstep_resource_key", table_name="runstep")
    op.drop_column("runstep", "resource_key")
    op.drop_column("runstep", "lease_token")
