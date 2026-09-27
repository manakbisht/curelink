import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Round, RoundResult


class RoundRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, session_id: uuid.UUID, round_number: int, sequence: list[str]) -> Round:
        round_ = Round(session_id=session_id, round_number=round_number, sequence=sequence, result=RoundResult.PENDING)
        self.db.add(round_)
        await self.db.flush()
        return round_

    async def get_by_id(self, round_id: uuid.UUID) -> Round | None:
        return await self.db.get(Round, round_id)

    async def get_current_round(self, session_id: uuid.UUID) -> Round | None:
        """The most recent round of a session, whatever its result."""
        stmt = select(Round).where(Round.session_id == session_id).order_by(Round.round_number.desc()).limit(1)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_for_session(self, session_id: uuid.UUID) -> list[Round]:
        stmt = select(Round).where(Round.session_id == session_id).order_by(Round.round_number)
        return list((await self.db.execute(stmt)).scalars())

    async def save_result(self, round_: Round, result: RoundResult) -> None:
        round_.result = result
        await self.db.flush()
