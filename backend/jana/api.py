"""Offline REST adapters and same-origin operator interface."""

from contextlib import asynccontextmanager
import errno
import ipaddress
import json
import os
from pathlib import Path
import socket
from time import perf_counter

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from . import __version__
from .contracts import InputError, MAX_BODY_BYTES, validate_batch, validate_output

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ("/parse", "/batch", "/api/parse", "/api/v1/parse")


def _install_offline_socket_guard() -> None:
    """Block outbound TCP/UDP connects while preserving loopback health/API."""

    if getattr(socket.socket, "_jana_offline_guard", False):
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyaddr = socket.gethostbyaddr
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex
    original_getnameinfo = socket.getnameinfo
    original_sendto = socket.socket.sendto
    original_sendmsg = getattr(socket.socket, "sendmsg", None)

    def allowed(address: object) -> bool:
        if not isinstance(address, tuple) or not address:
            return True  # Unix-domain and other non-INET socket addresses.
        host = str(address[0])
        if host.casefold() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def allowed_reverse(address: object) -> bool:
        """Avoid NSS/DNS reverse lookups even for unusual loopback aliases."""

        if not isinstance(address, tuple) or not address:
            return True
        host = str(address[0]).casefold()
        return host in {"localhost", "127.0.0.1", "::1"}

    def guarded_connect(instance: socket.socket, address: object) -> None:
        if not allowed(address):
            raise PermissionError(errno.EACCES, "outbound network disabled")
        return original_connect(instance, address)

    def guarded_connect_ex(instance: socket.socket, address: object) -> int:
        if not allowed(address):
            return errno.EACCES
        return original_connect_ex(instance, address)

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object):
        flags = kwargs.get("flags", args[4] if len(args) > 4 else 0)
        passive_wildcard = bool(flags & socket.AI_PASSIVE) and str(host) in {"0.0.0.0", "::"}
        if host is not None and not passive_wildcard and not allowed((host, 0)):
            raise socket.gaierror(socket.EAI_NONAME, "external DNS disabled")
        return original_getaddrinfo(host, *args, **kwargs)

    def guarded_gethostbyname(host: object):
        if not allowed((host, 0)):
            raise socket.gaierror(socket.EAI_NONAME, "external DNS disabled")
        return original_gethostbyname(host)

    def guarded_gethostbyname_ex(host: object):
        if not allowed((host, 0)):
            raise socket.gaierror(socket.EAI_NONAME, "external DNS disabled")
        return original_gethostbyname_ex(host)

    def guarded_gethostbyaddr(host: object):
        if not allowed_reverse((host, 0)):
            raise socket.herror(1, "external DNS disabled")
        return original_gethostbyaddr(host)

    def guarded_getnameinfo(address: object, flags: int):
        if not allowed_reverse(address):
            raise socket.gaierror(socket.EAI_NONAME, "external DNS disabled")
        return original_getnameinfo(address, flags)

    def guarded_sendto(instance: socket.socket, data: object, *args: object):
        address = args[-1] if args else None
        if not allowed(address):
            raise PermissionError(errno.EACCES, "outbound network disabled")
        return original_sendto(instance, data, *args)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.socket.sendto = guarded_sendto
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyaddr = guarded_gethostbyaddr
    socket.gethostbyname = guarded_gethostbyname
    socket.gethostbyname_ex = guarded_gethostbyname_ex
    socket.getnameinfo = guarded_getnameinfo
    if original_sendmsg is not None:
        def guarded_sendmsg(
            instance: socket.socket,
            buffers: object,
            ancdata: object = (),
            flags: int = 0,
            address: object = None,
        ) -> int:
            if address is not None and not allowed(address):
                raise PermissionError(errno.EACCES, "outbound network disabled")
            if address is None:
                return original_sendmsg(instance, buffers, ancdata, flags)
            return original_sendmsg(instance, buffers, ancdata, flags, address)

        socket.socket.sendmsg = guarded_sendmsg
    socket.socket._jana_offline_guard = True


if os.environ.get("JANA_OFFLINE", "").casefold() in {"1", "true", "yes"}:
    _install_offline_socket_guard()


class BodyLimitMiddleware:
    """Bound actual streamed bytes, including requests without Content-Length."""

    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            total += len(message.get("body", b""))
            if total > self.limit:
                return await JSONResponse({"detail": {"code": "BODY_TOO_LARGE"}}, status_code=413)(scope, receive, send)
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


def _reject_constant(_):
    raise ValueError("NONFINITE_JSON")


async def read_records(request):
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        payload = await request.body()
    elif media_type == "multipart/form-data":
        # BodyLimitMiddleware has already bounded the complete request. Keep every
        # admissible file in RAM, including uploads above Starlette's 1 MiB default.
        form_parser = MultiPartParser(request.headers, request.stream(), max_files=1,
                                      max_fields=0, max_part_size=MAX_BODY_BYTES)
        form_parser.spool_max_size = MAX_BODY_BYTES
        try:
            form = await form_parser.parse()
        except MultiPartException:
            raise HTTPException(400, detail={"code": "INVALID_MULTIPART"}) from None
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile) or len(form) != 1:
                raise HTTPException(422, detail={"code": "EXPECTED_FILE_FIELD"})
            payload = await upload.read()
        finally:
            await form.close()
    else:
        raise HTTPException(415, detail={"code": "UNSUPPORTED_CONTENT_TYPE"})
    try:
        value = json.loads(payload, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, detail={"code": "INVALID_JSON"}) from None
    try:
        return validate_batch(value)
    except InputError as exc:
        raise HTTPException(422, detail=exc.detail()) from None


def create_app(*, parser=None, gazetteer_path=None, max_body_bytes=MAX_BODY_BYTES):
    @asynccontextmanager
    async def lifespan(app):
        app.state.ready = False
        started = perf_counter()
        app.state.limiter = anyio.CapacityLimiter(2)
        if parser is None:
            from .gazetteer import Gazetteer
            from .parser import parse_batch
            path = Path(gazetteer_path or os.environ.get("JANA_GAZETTEER_PATH", ROOT / "data/public/gazetteer.sqlite"))
            app.state.gazetteer = Gazetteer(path)
            app.state.parser = parse_batch
        else:
            app.state.gazetteer = None
            app.state.parser = parser
        app.state.startup_seconds = perf_counter() - started
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            if app.state.gazetteer is not None:
                app.state.gazetteer.close()

    app = FastAPI(title="Jana Address", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.ready = False
    app.add_middleware(BodyLimitMiddleware, limit=max_body_bytes)

    @app.get("/health/live")
    def live():
        return {"service": "jana-address", "status": "alive", "version": __version__}

    @app.get("/health/ready")
    def ready():
        if not app.state.ready:
            return JSONResponse({"service": "jana-address", "status": "starting"}, status_code=503)
        return {
            "service": "jana-address", "status": "ready", "version": __version__,
            "startup_seconds": app.state.startup_seconds,
            "gazetteer": getattr(app.state.gazetteer, "stats", {}),
        }

    async def process(request, *, explain=False):
        if not app.state.ready:
            raise HTTPException(503, detail={"code": "NOT_READY"})
        records = await read_records(request)
        started = perf_counter()

        def run():
            parsed = app.state.parser(records, gazetteer=app.state.gazetteer, explain=explain)
            public = [item["result"] for item in parsed] if explain else parsed
            canonical = validate_output(records, public)
            if explain:
                for item, result in zip(parsed, canonical):
                    item["result"] = result
                return {"records": parsed, "meta": {"count": len(records), "seconds": perf_counter() - started}}
            return canonical

        try:
            result = await anyio.to_thread.run_sync(run, limiter=app.state.limiter)
            response = JSONResponse(result, headers={"Cache-Control": "no-store"})
        except Exception:
            # Do not echo raw rows, parser traces or exception payloads to logs/clients.
            raise HTTPException(500, detail={"code": "PARSER_FAILURE"}) from None
        return response

    async def public_parse(request: Request):
        return await process(request)

    for path in ROUTES:
        app.add_api_route(path, public_parse, methods=["POST"], name="parse" + path.replace("/", "_"))

    @app.post("/internal/parse")
    async def internal_parse(request: Request):
        return await process(request, explain=True)

    app.mount("/", StaticFiles(directory=ROOT / "site", html=True), name="operator-ui")
    return app


app = create_app()
