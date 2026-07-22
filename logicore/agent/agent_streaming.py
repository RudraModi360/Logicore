"""
AgentStreamingMixin: Streaming and chat methods extracted from Agent.

Consolidates all chat and streaming API methods:
chat(), stream_run(), stream(), stream_sync(), cancel_run().

Agent inherits from this mixin to maintain the same public API.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Union

from logicore.stream.emitter import StreamEmitter
from logicore.stream.result import AgentRunResult
from logicore.stream.events import StreamEvent, StreamEventType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _recover_import_error(exc: NameError | ImportError) -> bool:
    """Attempt to recover from import-resolution failures.

    Stale .pyc files or transient import races can cause NameError/ImportError
    at runtime even though the module is valid.  Re-importing the offending
    module often resolves the issue for subsequent calls.
    """
    try:
        import importlib
        module_name = getattr(exc, "name", None)
        if module_name:
            importlib.invalidate_caches()
            importlib.import_module(module_name)
            logger.info(f"[AgentStreaming] Recovered import for '{module_name}'")
            return True
    except Exception as retry_exc:
        logger.debug(f"[AgentStreaming] Import recovery failed: {retry_exc}")
    return False


class AgentStreamingMixin:
    """Mixin providing chat and streaming methods for Agent.

    Expects the following attributes to be set on the host class:
    - _chat_orchestrator: ChatOrchestrator
    - input_enricher: InputEnricher
    - execution_log: List[str]
    - callbacks: Dict[str, Any]
    - _ensure_task_manager: Callable
    - _persist_session: Callable
    - create_session: Callable
    - get_session: Callable
    - sessions: Dict
    """

    async def chat(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = None,
        callbacks: Dict[str, Callable] = None,
        stream: bool = False,
        streaming_funct: Callable = None,
        generate_walkthrough: bool = False,
        new_session: bool = False,
        session_tags: Dict[str, str] = None,
        **kwargs
    ) -> str:
        if new_session:
            session_id = self.create_session(tags=session_tags)
        elif session_id is None:
            session_id = "default"
        if session_tags and session_id not in self.sessions:
            session = self.get_session(session_id)
            session.metadata["tags"] = session_tags

        self._ensure_task_manager(session_id)
        self.execution_log = []
        self.execution_log.append(f"Agent Started Task. User Request: {str(user_input)[:200]}")

        user_input = await self.input_enricher.enrich_async(user_input)

        try:
            response = await self._chat_orchestrator.run(
                user_input=user_input,
                session_id=session_id,
                callbacks=callbacks,
                stream=stream,
                streaming_funct=streaming_funct,
                generate_walkthrough=generate_walkthrough,
            )
        except asyncio.CancelledError:
            raise
        except (NameError, ImportError) as e:
            # Import-resolution failures (stale .pyc, circular import race).
            # Attempt recovery, then fall through to a graceful error message.
            logger.error(
                f"[Agent] Import resolution error: {e}\n"
                f"{traceback.format_exc()}"
            )
            if _recover_import_error(e):
                response = (
                    "An internal module loading issue was detected and "
                    "recovered. Please retry your request."
                )
            else:
                response = (
                    "An internal error occurred while loading a required "
                    "module. This is a framework bug — please restart the "
                    "session and try again."
                )
        except (TypeError, ValueError, KeyError) as e:
            # Structured errors from response parsing or tool result handling.
            # These are typically fixable by the LLM on the next iteration,
            # but since they escaped the orchestrator, log and return gracefully.
            logger.error(
                f"[Agent] Structured data error in chat loop: {e}\n"
                f"{traceback.format_exc()}"
            )
            response = (
                f"An internal data processing error occurred: {type(e).__name__}. "
                f"Please try rephrasing your request."
            )
        except RuntimeError as e:
            # Event loop issues, gateway connection failures, etc.
            logger.error(
                f"[Agent] Runtime error in chat loop: {e}\n"
                f"{traceback.format_exc()}"
            )
            response = (
                "A runtime error occurred during execution. "
                "Please try again in a moment."
            )
        except Exception as e:
            # Catch-all for truly unexpected errors — log full traceback,
            # never expose raw exception to the user.
            logger.error(
                f"[Agent] Unexpected error in chat loop: {type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}"
            )
            response = (
                f"An unexpected error occurred ({type(e).__name__}). "
                f"Please try again or start a new session."
            )

        self._persist_session(session_id, response)

        return response

    async def stream_run(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = None,
        callbacks: Dict[str, Callable] = None,
        generate_walkthrough: bool = False,
        new_session: bool = False,
        session_tags: Dict[str, str] = None,
        **kwargs,
    ) -> "AgentRunResult":
        """Start a streaming agent run and return an AgentRunResult."""
        if new_session:
            session_id = self.create_session(tags=session_tags)
        elif session_id is None:
            session_id = "default"
        if session_tags and session_id not in self.sessions:
            session = self.get_session(session_id)
            session.metadata["tags"] = session_tags

        self._ensure_task_manager(session_id)
        self.execution_log = []
        self.execution_log.append(f"Agent Started Task. User Request: {str(user_input)[:200]}")

        user_input = await self.input_enricher.enrich_async(user_input)

        active_callbacks = self.callbacks.copy()
        if callbacks:
            active_callbacks.update(callbacks)

        import uuid
        emitter = StreamEmitter(session_id=session_id, run_id=uuid.uuid4().hex)

        async def _produce() -> None:
            try:
                final = await self._chat_orchestrator.run(
                    user_input=user_input,
                    session_id=session_id,
                    callbacks=active_callbacks,
                    generate_walkthrough=generate_walkthrough,
                    emitter=emitter,
                )
                emitter.final = final
            except asyncio.CancelledError:
                raise
            except (NameError, ImportError) as e:
                logger.error(
                    f"[Agent] Import resolution error in stream: {e}\n"
                    f"{traceback.format_exc()}"
                )
                _recover_import_error(e)
                error_msg = (
                    "An internal module loading issue occurred during streaming. "
                    "Please retry your request."
                )
                try:
                    emitter.emit(StreamEvent.create(
                        StreamEventType.ERROR, {"message": error_msg, "recoverable": True}
                    ))
                except Exception:
                    pass
                emitter.final = error_msg
            except (TypeError, ValueError, KeyError) as e:
                logger.error(
                    f"[Agent] Structured data error in stream: {e}\n"
                    f"{traceback.format_exc()}"
                )
                error_msg = (
                    f"A data processing error occurred ({type(e).__name__}). "
                    f"Please try rephrasing your request."
                )
                try:
                    emitter.emit(StreamEvent.create(
                        StreamEventType.ERROR, {"message": error_msg, "recoverable": True}
                    ))
                except Exception:
                    pass
                emitter.final = error_msg
            except RuntimeError as e:
                logger.error(
                    f"[Agent] Runtime error in stream: {e}\n"
                    f"{traceback.format_exc()}"
                )
                error_msg = "A runtime error occurred. Please try again."
                try:
                    emitter.emit(StreamEvent.create(
                        StreamEventType.ERROR, {"message": error_msg, "recoverable": False}
                    ))
                except Exception:
                    pass
                emitter.final = error_msg
            except Exception as e:
                logger.error(
                    f"[Agent] Unexpected error in stream: {type(e).__name__}: {e}\n"
                    f"{traceback.format_exc()}"
                )
                error_msg = (
                    f"An unexpected error occurred ({type(e).__name__}). "
                    f"Please try again or start a new session."
                )
                try:
                    emitter.emit(StreamEvent.create(
                        StreamEventType.ERROR, {"message": error_msg, "recoverable": False}
                    ))
                except Exception:
                    pass
                emitter.final = error_msg
            finally:
                emitter.close()

        task = asyncio.ensure_future(_produce())
        return AgentRunResult(emitter, task, self)

    async def stream(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = None,
        **kwargs,
    ):
        """Convenience async generator that yields StreamEvent objects."""
        run = await self.stream_run(user_input, session_id=session_id, **kwargs)
        async for ev in run.stream_events():
            yield ev

    def cancel_run(self, run: "AgentRunResult") -> None:
        """Cancel an in-flight streaming run."""
        if run is not None:
            run.cancel()

    def stream_sync(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = "default",
        on_event: Callable = None,
        **kwargs,
    ) -> str:
        """Synchronous streaming — no server and no async framework required."""
        async def _drive():
            run = await self.stream_run(user_input, session_id=session_id, **kwargs)
            async for ev in run.stream_events():
                if on_event:
                    on_event(ev)
            return await run

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_drive())
        raise RuntimeError(
            "stream_sync() cannot be called from within a running event loop. "
            "Use `async for ev in agent.stream(...)` instead."
        )
