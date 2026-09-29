"""Safe normalized result helpers for outbound ACP delegation."""

from __future__ import annotations

from typing import Any


def _result(text: str, status: str) -> dict[str, Any]:
    return {"result": text, "status": status, "variables": {}}


def success(text: str) -> dict[str, Any]:
    return _result(text or "ACP agent completed without text output.", "success")


def permission_required() -> dict[str, Any]:
    return _result("ACP agent requires permission to continue.", "failed")


def startup_failure() -> dict[str, Any]:
    return _result("ACP agent could not be started.", "failed")


def protocol_failure() -> dict[str, Any]:
    return _result("ACP agent protocol communication failed.", "failed")


def timeout() -> dict[str, Any]:
    return _result("ACP agent did not respond before the timeout.", "failed")


def subprocess_exit() -> dict[str, Any]:
    return _result("ACP agent process exited unexpectedly.", "failed")
