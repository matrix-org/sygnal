from typing import Optional, Union

from opentracing.tracer import Tracer

class SpanContext:
    EMPTY_BAGGAGE: dict = ...
    @property
    def baggage(self): ...

class Span:
    def __init__(self, tracer: Tracer, context: SpanContext) -> None: ...
    @property
    def context(self): ...
    @property
    def tracer(self): ...
    def set_operation_name(self, operation_name: str) -> Span: ...
    def finish(self, finish_time: Optional[float] = ...) -> None: ...
    def set_tag(self, key: str, value: Union[str, bool, int, float]) -> Span: ...
    def log_kv(self, key_values: dict, timestamp: Optional[float] = ...) -> Span: ...
    def set_baggage_item(self, key: str, value: str) -> Span: ...
    def get_baggage_item(self, key: str) -> Optional[str]: ...
