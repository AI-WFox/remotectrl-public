import asyncio

from app.services.session_manager import SessionManager


class FakeWebSocket:
    async def accept(self) -> None:
        return None


def test_replaced_agent_socket_stays_online_when_old_socket_closes():
    async def scenario() -> None:
        manager = SessionManager()
        old_socket = FakeWebSocket()
        current_socket = FakeWebSocket()

        await manager.connect_agent("agent-1", old_socket)
        await manager.connect_agent("agent-1", current_socket)

        assert manager.disconnect_agent("agent-1", old_socket) is False
        assert manager.is_agent_online("agent-1") is True
        assert manager.disconnect_agent("agent-1", current_socket) is True
        assert manager.is_agent_online("agent-1") is False

    asyncio.run(scenario())