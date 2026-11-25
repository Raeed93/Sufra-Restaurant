"""Request and response shapes for the API.

Validation happens here, before any route code runs: a username with spaces
or a 50,000-character message is rejected with a 422 automatically.
"""
from pydantic import BaseModel, Field


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str


class PreferenceOut(BaseModel):
    id: int
    kind: str
    value: str


class MeOut(BaseModel):
    user: UserOut
    preferences: list[PreferenceOut]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: int | None = None


class ToolCallOut(BaseModel):
    name: str
    arguments: str
    output: str


class RecommendationOut(BaseModel):
    id: str
    name: str
    category: str
    price: float
    vegetarian: bool
    vegan: bool
    spice_level: int
    allergens: list[str]
    reason: str


class ChatOut(BaseModel):
    conversation_id: int
    reply: str
    recommendations: list[RecommendationOut]
    # Plain-language log of what the safety layer did this turn.
    safety_events: list[str]
    # Shown in the UI's "under the hood" panel: useful for the demo and for debugging.
    tool_calls: list[ToolCallOut]
    preferences: list[PreferenceOut]


class ConversationOut(BaseModel):
    id: int
    title: str | None
    created_at: str


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: str
    recommendations: list[RecommendationOut] = []
