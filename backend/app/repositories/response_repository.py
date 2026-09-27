import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Response


class ResponseRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, response_id: uuid.UUID, round_id: uuid.UUID, transcript: str, is_correct: bool) -> Response:
        """Insert a scored response. Raises IntegrityError if the id or the round was already used."""
        response = Response(id=response_id, round_id=round_id, transcript=transcript, is_correct=is_correct)
        self.db.add(response)
        await self.db.flush()
        return response

    async def get_by_id(self, response_id: uuid.UUID) -> Response | None:
        return await self.db.get(Response, response_id)
