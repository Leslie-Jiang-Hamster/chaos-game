from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Visibility = Literal["public", "private", "system"]
MessageSource = Literal["player", "llm", "system"]


@dataclass(slots=True)
class Role:
    role_id: str
    name: str
    age_job: str
    background: str
    public_persona: str
    motive: str
    core_trait: str
    secret: str
    taboo: str
    is_player: bool = False

    @property
    def short_label(self) -> str:
        return f"{self.role_id} {self.name}"


@dataclass(slots=True)
class Message:
    speaker_id: str
    speaker_name: str
    text: str
    visibility: Visibility
    source: MessageSource = "system"
    recipients: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Submission:
    role_id: str
    value: int


@dataclass(slots=True)
class RoundOneResult:
    average: float
    target: float
    rankings: list[tuple[Role, int, float]]
    survivors: list[Role]
    eliminated: list[Role]


@dataclass(slots=True)
class DecisionTrace:
    role_id: str
    role_name: str
    stage: str
    thought: str
    intent: str
    attitude: str
    action_name: str
    action_summary: str
