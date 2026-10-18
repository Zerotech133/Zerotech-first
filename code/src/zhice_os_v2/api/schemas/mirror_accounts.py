from pydantic import BaseModel, Field


class CreateMirrorAccountBody(BaseModel):
    strategy_id: str
    initial_capital: float = Field(gt=0)
