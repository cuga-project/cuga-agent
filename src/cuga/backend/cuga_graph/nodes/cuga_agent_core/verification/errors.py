"""Shared verifier errors, independent of retrieval and model transport."""


class PromptVerificationError(RuntimeError):
    """Raised when the verifier itself fails rather than rejecting a candidate."""
