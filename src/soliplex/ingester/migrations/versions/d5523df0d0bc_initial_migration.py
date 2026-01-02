"""initial migration

Revision ID: d5523df0d0bc
Revises: 
Create Date: 2026-01-02 17:22:26.908456

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd5523df0d0bc'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass
def downgrade() -> None:
    pass
    # ### end Alembic commands ###
