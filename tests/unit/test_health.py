from aiohttp.test_utils import TestClient, TestServer

from jargonator.health import HealthState, build_health_app


async def ok() -> bool:
    return True


async def broken() -> bool:
    raise RuntimeError("db gone")


async def fetch(state: HealthState) -> tuple[int, dict[str, object]]:
    async with TestClient(TestServer(build_health_app(state))) as client:
        response = await client.get("/healthz")
        return response.status, await response.json()


async def test_all_ok() -> None:
    assert await fetch(HealthState(socket_check=ok, db_check=ok)) == (
        200,
        {"status": "ok", "socket": "connected", "db": "ok"},
    )


async def test_socket_down() -> None:
    async def down() -> bool:
        return False

    status, body = await fetch(HealthState(socket_check=down, db_check=ok))
    assert status == 503 and body["socket"] == "disconnected" and body["status"] == "unavailable"


async def test_db_error() -> None:
    status, body = await fetch(HealthState(socket_check=ok, db_check=broken))
    assert status == 503 and body["db"] == "error"


async def test_before_startup_completes() -> None:
    status, body = await fetch(HealthState(db_check=ok))
    assert status == 503 and body["socket"] == "disconnected"
