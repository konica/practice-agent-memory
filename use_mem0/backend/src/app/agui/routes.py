"""The AG-UI run endpoint and the gate standing in front of it.

`add_langgraph_fastapi_endpoint` mounts its own route, so there is no handler
signature to hang dependencies off. The gate is middleware instead, and it has
to be: LangGraph's checkpointer has no concept of users, so without this check
anyone who knows a `thread_id` could read and append to another user's
conversation.
"""

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..auth.session import SESSION_COOKIE, resolve_session
from ..conversations.ownership import owns_conversation

AGENT_NAME = "mem0-chatbot"
AGENT_PATH = "/agent"


def guard_agent_request(request: Request, thread_id: str | None) -> tuple[str, str]:
    """Resolve the caller and the thread they may run against, or raise.

    404 rather than 403 for a thread the caller does not own, and the same 404
    for a thread that does not exist: the two must be indistinguishable, or the
    status code confirms that another user's conversation is there.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    settings = request.app.state.settings
    user_sub = (
        resolve_session(request.app.state.pool, cookie, settings.session_secret)
        if cookie
        else None
    )
    if user_sub is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not thread_id:
        raise HTTPException(status_code=400, detail="threadId is required")
    if not owns_conversation(request.app.state.pool, thread_id, user_sub):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return user_sub, thread_id


def identify_run(payload: dict, user_sub: str, memory_enabled: bool) -> dict:
    """Return the payload with the run's identity taken from the session.

    `user_id` scopes every mem0 read and write the graph makes, so it is the
    server's to set, never the client's: a caller who could name it would be
    reading another user's memories through the very endpoint this gate exists
    to protect. Whatever the request claimed is overwritten, not merged.

    AG-UI's `state` becomes the graph's input state, which is how these reach
    the nodes.
    """
    state = payload.get("state")
    if not isinstance(state, dict):
        state = {}
    return {
        **payload,
        "state": {**state, "user_id": user_sub, "memory_enabled": memory_enabled},
    }


class AgentAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate and authorise an AG-UI run before the adapter sees it."""

    async def dispatch(self, request: Request, call_next):
        if request.method != "POST" or request.url.path != AGENT_PATH:
            return await call_next(request)

        try:
            payload = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"detail": "threadId is required"}, status_code=400)

        try:
            # `threadId` is a top-level field of AG-UI's RunAgentInput, which
            # `ag-ui-langgraph` maps onto config["configurable"]["thread_id"] —
            # so this is the id the run will actually read and write.
            user_sub, thread_id = guard_agent_request(request, payload.get("threadId"))
        except HTTPException as denied:
            # FastAPI's exception handler lives inside this middleware, so an
            # HTTPException raised here would surface as a 500. Render it.
            return JSONResponse({"detail": denied.detail}, status_code=denied.status_code)

        request.state.user_sub = user_sub
        request.state.thread_id = thread_id

        # Hand the adapter a body carrying the identity we just verified.
        # `_CachedRequest.wrapped_receive` replays `_body` to whatever is
        # downstream, so replacing it here is what the middleware base class
        # already does with the body we consumed above — only with our value.
        request._body = json.dumps(
            identify_run(payload, user_sub, request.app.state.settings.memory_retrieval_enabled)
        ).encode()
        return await call_next(request)


def add_agent_gate(app: FastAPI) -> None:
    """Install the gate while the app is still being built.

    Separate from `mount_agent_endpoint` because the two happen at different
    times: Starlette freezes the middleware stack on the first request, so
    middleware must be added before startup, while the endpoint needs the graph
    that startup builds. Routes, unlike middleware, can be added later.
    """
    app.add_middleware(AgentAuthMiddleware)


def mount_agent_endpoint(app: FastAPI, graph) -> None:
    """Mount `POST /agent`, streaming AG-UI events from the compiled graph.

    Imported here rather than at module scope so that using the gate on its own
    does not drag the whole adapter in.
    """
    from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint

    add_langgraph_fastapi_endpoint(
        app, LangGraphAgent(name=AGENT_NAME, graph=graph), AGENT_PATH
    )
