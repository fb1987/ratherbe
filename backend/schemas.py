from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime

# ----- Requests -----

class PlayerSliders(BaseModel):
    creativity: float = 0.5
    absurdism: float = 0.5
    romance: float = 0.2
    pop_culture: float = 0.3
    spice: float = 0.1
    decade: Literal["1970s","1980s","1990s","2000s","2010s","2020s","2030s"]="2000s"

class CreateSessionReq(BaseModel):
    players: List[Dict[str, Any]]  # each: {"name":"A","sliders": PlayerSliders}
    pg13: bool = True

class VoteReq(BaseModel):
    questionId: str
    chosen: Literal["x","y"]  # <-- NEW: user picks X or Y
    player: Optional[str] = None
    like: Optional[bool] = None  # kept optional/back-compat

class MoreReq(BaseModel):
    reason: Literal["deck_low","manual","first_seed_failed"] = "manual"

# ----- DTOs -----

class QuestionDTO(BaseModel):
    id: str = Field(..., alias="_id")
    text: str
    x: str
    y: str
    tags: List[str] = []
    theme: Optional[str] = None
    imageDataUrl: Optional[str] = None  # NEW


class NextResp(BaseModel):
    sessionId: str
    remaining: int
    question: QuestionDTO

class SessionResp(BaseModel):
    sessionId: str
    createdAt: datetime
    pg13: bool
