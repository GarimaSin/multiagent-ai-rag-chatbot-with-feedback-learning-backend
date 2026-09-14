from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationInput(InputModel):
    title: str = Field("New conversation", min_length=1, max_length=100)


class ChatInput(InputModel):
    conversation_id: UUID
    request_id: UUID
    message: str = Field(min_length=1, max_length=6000)
    mode: Literal["knowledge", "general"] = "knowledge"
    quality: Literal["fast", "verified"] = "fast"

    @field_validator("message")
    @classmethod
    def clean_message(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be blank")
        if "\x00" in value:
            raise ValueError("NUL characters are not supported")
        return value


class DocumentInput(InputModel):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=20, max_length=500_000)

    @field_validator("title", "text")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Blank or binary text is not supported")
        return value


class FeedbackInput(InputModel):
    message_id: UUID
    rating: Literal[-1, 1]
    correction: str = Field("", max_length=3000)
    consent: bool = False
    evidence_chunk_id: UUID | None = None
    evidence_quote: str = Field("", max_length=3000)

    @model_validator(mode="after")
    def require_evidence(self):
        self.correction = self.correction.strip()
        if self.correction and (not self.consent or self.evidence_chunk_id is None or len(self.evidence_quote.strip()) < 20):
            raise ValueError("A learning correction needs consent, a source chunk, and an exact quote of at least 20 characters")
        return self


class ReviewInput(InputModel):
    action: Literal["approve", "reject"]
    note: str = Field(min_length=5, max_length=2000)
