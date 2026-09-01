"""The AG-UI agent that reports a failed run instead of falling silent.

`ag_ui_langgraph` emits `RUN_ERROR` only for in-band `"error"` events from
`astream_events`. A node that *raises* — which is exactly what `call_model` does
once its one retry is spent — propagates out of the adapter's generator, so the
SSE response simply stops mid-stream with no terminal event. The client has no
way to tell that from a reply still on its way, and shows an assistant turn that
never arrives: a silent hang where the UI owes the user an error and a Retry.

Emitting the terminal event ourselves is what turns that into a reported
failure. The failure policy upstream is unchanged: the model call still retries
once and then propagates; this only makes the propagation visible.
"""

import logging

from ag_ui.core import EventType, RunErrorEvent
from ag_ui_langgraph import LangGraphAgent

logger = logging.getLogger(__name__)

# What the user is told. Deliberately not the exception text: it reaches a
# browser, and the detail worth having is already in the server log.
RUN_FAILED_MESSAGE = "The assistant could not finish that reply."


class ReportingLangGraphAgent(LangGraphAgent):
    """`LangGraphAgent`, plus a terminal `RUN_ERROR` when the run raises."""

    async def run(self, input):
        try:
            async for event in super().run(input):
                yield event
        except Exception:
            # `except Exception` deliberately excludes CancelledError: a client
            # that disconnected is not a failure to report, and yielding into a
            # cancelled generator would fail on its own.
            logger.exception("agent run failed; reporting RUN_ERROR to the client")
            yield RunErrorEvent(type=EventType.RUN_ERROR, message=RUN_FAILED_MESSAGE)
