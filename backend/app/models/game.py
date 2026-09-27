import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


class SessionStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"  # ended by the player or all rounds cleared
    FAILED = "FAILED"  # a wrong answer ended the game


class RoundResult(enum.StrEnum):
    PENDING = "PENDING"
    CORRECT = "CORRECT"
    WRONG = "WRONG"
    ABANDONED = "ABANDONED"  # session ended before the round was answered


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class GameSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    player_name: Mapped[str] = mapped_column(String(40))
    status: Mapped[SessionStatus] = mapped_column(
        _enum(SessionStatus, "session_status"), default=SessionStatus.ACTIVE, index=True
    )
    score: Mapped[int] = mapped_column(Integer, default=0)
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    rounds: Mapped[list["Round"]] = relationship(
        back_populates="session", order_by="Round.round_number", cascade="all, delete-orphan"
    )


class Round(Base):
    __tablename__ = "rounds"
    # One row per round number: a retried "start next round" cannot create a duplicate.
    __table_args__ = (UniqueConstraint("session_id", "round_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    round_number: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[list[str]] = mapped_column(JSONB)
    result: Mapped[RoundResult] = mapped_column(_enum(RoundResult, "round_result"), default=RoundResult.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[GameSession] = relationship(back_populates="rounds")
    response: Mapped["Response | None"] = relationship(back_populates="round", uselist=False)


class Response(Base):
    __tablename__ = "responses"

    # The id is supplied by the caller and doubles as an idempotency key:
    # replaying the same response id can never be scored twice.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # Unique: a round is scored by at most one response, even if two different
    # responses race for it.
    round_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), unique=True)
    transcript: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    round: Mapped[Round] = relationship(back_populates="response")
