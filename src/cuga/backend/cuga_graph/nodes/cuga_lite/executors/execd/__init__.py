from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .execd_executor import ExecdExecutor

__all__ = ['ExecdExecutor']


def __getattr__(name: str):
    # Lazy (PEP 562): importing ``execd.connection`` must not pull in the executor
    # and its dependencies; the server imports the connection on every start.
    if name == 'ExecdExecutor':
        from .execd_executor import ExecdExecutor

        return ExecdExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
