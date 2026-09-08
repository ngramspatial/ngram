"""Authenticated preview/download/control transport. It never initiates inference."""

import base64

from aiohttp import web

from ngram.presence.tools.execution_rpc import get_execution_client_for_entity


def register_blender_routes(app, check_token):
    async def handle(request):
        if not check_token(request):
            raise web.HTTPForbidden()
        client = get_execution_client_for_entity(request.app["entity"])
        ident = request.match_info["project"]
        command = request.match_info["command"]
        if command == "status" and request.method == "GET":
            result = await client.call("blender", {"command": "status", "project_id": ident})
            # Disk paths, source code, and script output are not part of the surface UI.
            return web.json_response({k: result.get(k) for k in (
                "ok", "project_id", "name", "state", "error", "snapshot", "job_id",
            )}, headers={"Cache-Control": "no-store"})
        if command == "stop" and request.method == "POST":
            result = await client.call("blender", {"command": "stop", "project_id": ident})
            return web.json_response({"ok": result.get("ok"), "state": result.get("state"), "error": result.get("error")})
        raise web.HTTPNotFound()

    async def artifact(request):
        if not check_token(request):
            raise web.HTTPForbidden()
        client = get_execution_client_for_entity(request.app["entity"])
        name = request.match_info["name"]
        render_id = request.match_info.get("render")
        args = {"command": "artifact", "project_id": request.match_info["project"], "offset": 0}
        if render_id:
            args["render_id"] = render_id
        else:
            args.update(revision=int(request.match_info["revision"]), name=name)
        chunk = await client.call("blender", args)
        if not chunk.get("ok"):
            raise web.HTTPNotFound()
        size = chunk.get("size", 0)
        maximum = 4 * 1024 * 1024 if render_id else 32 * 1024 * 1024 if name == "preview.glb" else 512 * 1024 * 1024
        if not isinstance(size, int) or size <= 0 or size > maximum:
            raise web.HTTPRequestEntityTooLarge(max_size=maximum, actual_size=size)
        response = web.StreamResponse(headers={
            "Content-Type": "image/jpeg" if render_id else "model/gltf-binary" if name == "preview.glb" else "application/octet-stream",
            "Content-Length": str(size), "Cache-Control": "private, max-age=31536000, immutable",
            "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff",
        })
        await response.prepare(request)
        offset = 0
        while offset < size:
            if not chunk.get("ok") or chunk.get("offset") != offset or chunk.get("size") != size:
                raise ConnectionError("Blender artifact transfer changed or failed")
            data = base64.b64decode(chunk.get("data", ""), validate=True)
            if not data or len(data) > min(512 * 1024, size - offset):
                raise ConnectionError("Invalid Blender artifact chunk")
            await response.write(data)
            offset += len(data)
            if offset < size:
                chunk = await client.call("blender", {**args, "offset": offset})
        await response.write_eof()
        return response

    app.router.add_route("*", "/blender/{project:[a-zA-Z0-9_-]{1,64}}/{command:status|stop}", handle)
    app.router.add_get("/blender/{project:[a-zA-Z0-9_-]{1,64}}/renders/{render:[a-f0-9]{32}}/{name:view.jpg}", artifact)
    app.router.add_get("/blender/{project:[a-zA-Z0-9_-]{1,64}}/{revision:[0-9]+}/{name:preview.glb|project.blend}", artifact)
