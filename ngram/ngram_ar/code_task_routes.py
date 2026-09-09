"""Authenticated coding goal inspection and controls, independent of chat sockets."""

from aiohttp import web

from ngram.presence.tools.code_task import manager_for


def register_code_task_routes(app, check_token):
    async def handle(request):
        if not check_token(request):
            raise web.HTTPForbidden()
        entity = request.app["entity"]
        enabled = getattr(entity, "_tool_enabled", None)
        if enabled is not None and not enabled("code_task", True):
            raise web.HTTPNotFound()
        manager = manager_for(entity)
        task_id = request.match_info.get("task_id", "")
        if request.method == "GET":
            result = manager.status(task_id)
        elif request.match_info["action"] == "cancel":
            result = await manager.cancel(task_id)
        else:
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("expected an object")
                if not isinstance(body.get("instructions", ""), str):
                    raise ValueError("instructions must be text")
                if request.match_info["action"] == "steer":
                    result = await manager.steer(task_id, str(body.get("instructions", "")))
                else:
                    result = await manager.resume(
                        task_id, str(body.get("instructions", "")), int(body.get("additional_seconds", 21600)),
                    )
            except (ValueError, TypeError):
                raise web.HTTPBadRequest(text="Invalid goal control request") from None
        return web.json_response(result, headers={"Cache-Control": "no-store"}, status=200 if result.get("ok") else 400)

    app.router.add_get("/code-tasks", handle)
    app.router.add_get("/code-tasks/{task_id:[a-f0-9]{32}}", handle)
    app.router.add_post("/code-tasks/{task_id:[a-f0-9]{32}}/{action:resume|cancel|steer}", handle)
