from enum import StrEnum
from typing import ClassVar


class JarvisState(StrEnum):
    IDLE = "IDLE"
    THINKING = "THINKING"
    RESPONDING = "RESPONDING"


class InvalidStateTransition(ValueError):
    pass


class JarvisStateMachine:
    _allowed: ClassVar[dict[JarvisState, set[JarvisState]]] = {
        JarvisState.IDLE: {JarvisState.THINKING},
        JarvisState.THINKING: {JarvisState.RESPONDING, JarvisState.IDLE},
        JarvisState.RESPONDING: {JarvisState.IDLE},
    }

    def __init__(self) -> None:
        self.state = JarvisState.IDLE

    def transition(self, next_state: JarvisState) -> None:
        if next_state not in self._allowed[self.state]:
            raise InvalidStateTransition(f"{self.state} -> {next_state}")
        self.state = next_state
