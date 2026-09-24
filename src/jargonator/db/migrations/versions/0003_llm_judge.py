"""Spec v1.1: guesses get an integer judge score (0-100) instead of a float similarity,
and rounds get llm_retry_at for the essential-LLM retry (spec §3.11).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("guesses") as batch:
        batch.drop_column("similarity")
        batch.add_column(sa.Column("score", sa.Integer(), nullable=True))
    with op.batch_alter_table("rounds") as batch:
        batch.add_column(sa.Column("llm_retry_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("rounds") as batch:
        batch.drop_column("llm_retry_at")
    with op.batch_alter_table("guesses") as batch:
        batch.drop_column("score")
        batch.add_column(sa.Column("similarity", sa.Float(), nullable=True))
