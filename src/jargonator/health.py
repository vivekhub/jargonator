"""GET /healthz for Docker/Kubernetes health checks (spec.md §14)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from aiohttp import web

Check = Callable[[], Awaitable[bool]]

log = structlog.get_logger()


async def _not_ready() -> bool:
    return False


@dataclass
class HealthState:
    """Checks are swapped in as startup progresses (the socket check exists only once
    the Socket Mode handler is connected)."""

    socket_check: Check = _not_ready
    db_check: Check = _not_ready


async def _passes(check: Check) -> bool:
    try:
        return await check()
    except Exception:
        log.warning("health_check_error", exc_info=True)
        return False


def build_health_app(state: HealthState) -> web.Application:
    async def healthz(_: web.Request) -> web.Response:
        socket_ok = await _passes(state.socket_check)
        db_ok = await _passes(state.db_check)
        body = {
            "status": "ok" if socket_ok and db_ok else "unavailable",
            "socket": "connected" if socket_ok else "disconnected",
            "db": "ok" if db_ok else "error",
        }
        return web.json_response(body, status=200 if socket_ok and db_ok else 503)

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    return app


async def start_health_server(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host="0.0.0.0", port=port).start()
    return runner
