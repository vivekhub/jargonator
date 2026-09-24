"""Initial schema (spec.md §10).

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ID = sa.String(36)
SLACK_ID = sa.String(32)
ENUM = sa.String(32)


def upgrade() -> None:
    op.create_table(
        "games",
        sa.Column("id", ID, primary_key=True),
        sa.Column("channel_id", SLACK_ID, nullable=False),
        sa.Column("host_user_id", SLACK_ID, nullable=False),
        sa.Column("state", ENUM, nullable=False),
        sa.Column("created_by", SLACK_ID, nullable=False),
        sa.Column("guess_seconds", sa.Integer(), nullable=False),
        sa.Column("writer_seconds", sa.Integer(), nullable=False),
        sa.Column("join_window_seconds", sa.Integer(), nullable=False),
        sa.Column("lobby_message_ts", SLACK_ID, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("end_reason", sa.String(16), nullable=True),
        sa.Column("lobby_deadline", sa.DateTime(), nullable=True),
        sa.Column("idle_deadline", sa.DateTime(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_games_active_channel",
        "games",
        ["channel_id"],
        unique=True,
        sqlite_where=sa.text("state != 'ENDED'"),
    )

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("game_id", ID, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("user_id", SLACK_ID, nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("round_wins", sa.Integer(), nullable=False),
        sa.Column("consecutive_misses", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.Column("left_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("game_id", "user_id", name="uq_players_game_user"),
    )
    op.create_index("ix_players_user_id", "players", ["user_id"])

    op.create_table(
        "turn_order",
        sa.Column("game_id", ID, sa.ForeignKey("games.id"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("cycle_no", sa.Integer(), nullable=False),
        sa.Column("user_id", SLACK_ID, nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "rounds",
        sa.Column("id", ID, primary_key=True),
        sa.Column("game_id", ID, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("writer_user_id", SLACK_ID, nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("level", ENUM, nullable=True),
        sa.Column("sentence", sa.Text(), nullable=True),
        sa.Column("jargon", sa.Text(), nullable=True),
        sa.Column("quip", sa.Text(), nullable=True),
        sa.Column("writer_deadline", sa.DateTime(), nullable=True),
        sa.Column("writer_reminder_at", sa.DateTime(), nullable=True),
        sa.Column("guess_deadline", sa.DateTime(), nullable=True),
        sa.Column("host_claim_at", sa.DateTime(), nullable=True),
        sa.Column("status_message_ts", SLACK_ID, nullable=True),
        sa.Column("results_message_ts", SLACK_ID, nullable=True),
        sa.Column("writer_bonus_awarded", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("game_id", "number", name="uq_rounds_game_number"),
    )

    op.create_table(
        "round_guessers",
        sa.Column("round_id", ID, sa.ForeignKey("rounds.id"), primary_key=True),
        sa.Column("user_id", SLACK_ID, primary_key=True),
        sa.Column("dm_channel_id", SLACK_ID, nullable=False),
        sa.Column("dm_message_ts", SLACK_ID, nullable=True),
    )

    op.create_table(
        "guesses",
        sa.Column("id", ID, primary_key=True),
        sa.Column("round_id", ID, sa.ForeignKey("rounds.id"), nullable=False),
        sa.Column("user_id", SLACK_ID, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("moderated_out", sa.Boolean(), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.UniqueConstraint("round_id", "user_id", name="uq_guesses_round_user"),
    )


def downgrade() -> None:
    op.drop_table("guesses")
    op.drop_table("round_guessers")
    op.drop_table("rounds")
    op.drop_table("turn_order")
    op.drop_index("ix_players_user_id", table_name="players")
    op.drop_table("players")
    op.drop_index("uq_games_active_channel", table_name="games")
    op.drop_table("games")
