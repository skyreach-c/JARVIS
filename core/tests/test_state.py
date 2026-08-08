import pytest

from jarvis_core.state import InvalidStateTransition, JarvisState, JarvisStateMachine


def test_state_machine_follows_heartbeat_sequence() -> None:
    machine = JarvisStateMachine()

    assert machine.state is JarvisState.IDLE

    machine.transition(JarvisState.THINKING)
    assert machine.state is JarvisState.THINKING

    machine.transition(JarvisState.RESPONDING)
    assert machine.state is JarvisState.RESPONDING

    machine.transition(JarvisState.IDLE)
    assert machine.state is JarvisState.IDLE


def test_state_machine_rejects_skipping_thinking() -> None:
    machine = JarvisStateMachine()

    with pytest.raises(InvalidStateTransition):
        machine.transition(JarvisState.RESPONDING)
