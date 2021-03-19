import logging
import types
from asyncio import AbstractEventLoop, transports
from asyncio.protocols import BaseProtocol, Protocol
from asyncio.transports import Transport
from contextvars import Context
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TimelessEventLoopWrapper:
    @property  # type: ignore
    def __class__(self):
        """
        Fakes isinstance(this, AbstractEventLoop) so we can set_event_loop
        without fail.
        """
        return self._wrapped_loop.__class__

    def __init__(self, wrapped_loop: AbstractEventLoop):
        self._wrapped_loop = wrapped_loop
        self._time = 0.0
        self._to_be_called: List[Tuple[float, Any, Any, Any]] = []

    def advance(self, time_delta: float):
        target_time = self._time + time_delta
        logger.debug(
            "advancing from %f by %f (%d in queue)",
            self._time,
            time_delta,
            len(self._to_be_called),
        )
        while self._time < target_time and self._to_be_called:
            # pop off the next callback from the queue
            next_time, next_callback, args, _context = self._to_be_called[0]
            if next_time > target_time:
                # this isn't allowed to run yet
                break
            logger.debug("callback at %f on %r", next_time, next_callback)
            self._to_be_called = self._to_be_called[1:]
            self._time = next_time
            next_callback(*args)

        # no more tasks can run now but advance to the time anyway
        self._time = target_time

    def __getattr__(self, item: str):
        """
        We use this to delegate other method calls to the real EventLoop.
        """
        value = getattr(self._wrapped_loop, item)
        if isinstance(value, types.MethodType):
            # rebind this method to be called on us
            # this makes the wrapped class use our overridden methods when
            # available.
            # we have to do this because methods are bound to the underlying
            # event loop, which will call `self.call_later` or something
            # which won't normally hit us because we are not an actual subtype.
            return types.MethodType(value.__func__, self)
        else:
            return value

    def call_later(
        self,
        delay: float,
        callback: Callable,
        *args: Any,
        context: Optional[Context] = None,
    ):
        self.call_at(self._time + delay, callback, *args, context=context)

        # We're meant to return a canceller, but can cheat and return a no-op one
        # instead.
        class _Canceller:
            def cancel(self):
                pass

        return _Canceller()

    def call_at(
        self,
        when: float,
        callback: Callable,
        *args: Any,
        context: Optional[Context] = None,
    ):
        logger.debug(f"Calling {callback} at %f...", when)
        self._to_be_called.append((when, callback, args, context))

        # re-sort list in ascending time order
        self._to_be_called.sort(key=lambda x: x[0])

    def call_soon(
        self, callback: Callable, *args: Any, context: Optional[Context] = None
    ):
        return self.call_later(0, callback, *args, context=context)

    def time(self) -> float:
        return self._time


class MockTransport(Transport):
    """
    A transport intended to be driven by tests.
    Stores received data into a buffer.
    """

    def __init__(self):
        # Holds bytes received
        self.buffer = b""

        # Whether we reached the end of file/stream
        self.eofed = False

        # Whether the connection was aborted
        self.aborted = False

        # The protocol attached to this transport
        self.protocol = None

        # Whether this transport was closed
        self.closed = False

        # We need to explicitly mark that this connection allows start tls,
        # otherwise `loop.start_tls` will raise an exception.
        self._start_tls_compatible = True

    def reset_mock(self) -> None:
        self.buffer = b""
        self.eofed = False
        self.aborted = False
        self.closed = False

    def is_reading(self) -> bool:
        return True

    def pause_reading(self) -> None:
        pass  # NOP

    def resume_reading(self) -> None:
        pass  # NOP

    def set_write_buffer_limits(self, high: int = None, low: int = None) -> None:
        pass  # NOP

    def get_write_buffer_size(self) -> int:
        """Return the current size of the write buffer."""
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        self.buffer += data

    def write_eof(self) -> None:
        self.eofed = True

    def can_write_eof(self) -> bool:
        return True

    def abort(self) -> None:
        self.aborted = True

    def pretend_to_receive(self, data: bytes) -> None:
        proto = self.get_protocol()
        assert isinstance(proto, Protocol)
        proto.data_received(data)

    def set_protocol(self, protocol: BaseProtocol) -> None:
        self.protocol = protocol

    def get_protocol(self) -> BaseProtocol:
        assert isinstance(self.protocol, BaseProtocol)
        return self.protocol

    def close(self) -> None:
        self.closed = True


class MockProtocol(Protocol):
    """
    A protocol intended to be driven by tests.
    Stores received data into a buffer.
    """

    def __init__(self):
        self._to_transmit = b""
        self.received_bytes = b""
        self.transport = None

    def data_received(self, data: bytes) -> None:
        self.received_bytes += data

    def connection_made(self, transport: transports.BaseTransport) -> None:
        assert isinstance(transport, Transport)
        self.transport = transport
        if self._to_transmit:
            transport.write(self._to_transmit)

    def write(self, data: bytes) -> None:
        if self.transport:
            self.transport.write(data)
        else:
            self._to_transmit += data


class EchoProtocol(Protocol):
    """A protocol that immediately echoes all data it receives"""

    def __init__(self):
        self._to_transmit = b""
        self.received_bytes = b""
        self.transport = None

    def data_received(self, data: bytes) -> None:
        self.received_bytes += data
        assert self.transport
        self.transport.write(data)

    def connection_made(self, transport: transports.BaseTransport) -> None:
        assert isinstance(transport, Transport)
        self.transport = transport
        if self._to_transmit:
            transport.write(self._to_transmit)

    def write(self, data: bytes) -> None:
        if self.transport:
            self.transport.write(data)
        else:
            self._to_transmit += data
