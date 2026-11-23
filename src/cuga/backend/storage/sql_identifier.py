"""Check a table name before it goes directly into SQL.

PostgreSQL cannot take a table name as a query parameter, so the embedding store puts
it into DDL and DML text. Policy collection names for named agents also pass through
here, because they come from an agent id in the request. Restricting names to
lower-case ASCII also makes the 63-character limit equal to PostgreSQL's 63-byte limit.

Code scanning treats the return value of ``safe_sql_identifier`` as safe for SQL (see
.github/codeql/extensions/workspace-path-sanitizers/sanitizers.model.yml). The function
is in its own module because code scanning finds it only where another module imports
it: a call from the module that defines it is not matched. If you rename or move it,
update that file too, or the SQL that uses its result is reported again.
"""

import re

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def safe_sql_identifier(name: str) -> str:
    """Return ``name`` if it is safe to put directly into SQL as a table name; raise otherwise."""
    if _SAFE_IDENTIFIER_RE.fullmatch(name) is None:
        raise ValueError(
            f"collection_name {name!r} is not a valid PostgreSQL identifier. "
            "Must match ^[a-z][a-z0-9_]{0,62}$ (lowercase-leading, max 63 characters)."
        )
    return name
