"""Deterministic schema logic for the table designer demo.

Everything here is a pure function of its inputs: the same request always
yields the same proposal, the same diff and the same DDL. No clock, no
randomness, no I/O. The MCP server in ``server.py`` only wraps these.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable

IDENTIFIER_PATTERN = r"^[a-z_][a-z0-9_]{0,62}$"
_IDENTIFIER_RE = re.compile(IDENTIFIER_PATTERN)

COLUMN_TYPES: tuple[str, ...] = (
    "INTEGER",
    "BIGINT",
    "VARCHAR(255)",
    "TEXT",
    "BOOLEAN",
    "DATE",
    "TIMESTAMP",
    "DECIMAL(10,2)",
    "UUID",
    "JSON",
)

# Ordered rules: the first matching rule wins. (pattern, type, nullable)
_TYPE_RULES: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"(^|_)id$"), "INTEGER", False),
    (re.compile(r"^(is|has|can)_|_flag$|^active$|^enabled$"), "BOOLEAN", False),
    (re.compile(r"(_at|_time|timestamp)$"), "TIMESTAMP", True),
    (re.compile(r"(date|_on|birthday|dob)$"), "DATE", True),
    (re.compile(r"(price|amount|cost|total|balance|salary|revenue)"), "DECIMAL(10,2)", True),
    (re.compile(r"(phone|mobile|fax|email|zip|postal|iban|sku)"), "VARCHAR(255)", True),
    (re.compile(r"(count|qty|quantity|age|year|number|num|rank|score)"), "INTEGER", True),
    (re.compile(r"(description|notes|comment|body|bio|address)"), "TEXT", True),
    (re.compile(r"(uuid|guid)"), "UUID", False),
    (re.compile(r"(metadata|settings|payload|attributes)"), "JSON", True),
    (re.compile(r"(email|name|title|phone|city|country|status|code|url|username)"), "VARCHAR(255)", True),
)


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Column":
        return cls(
            name=str(data.get("name", "")).strip(),
            type=str(data.get("type", "VARCHAR(255)")).strip().upper(),
            nullable=bool(data.get("nullable", True)),
            primary_key=bool(data.get("primary_key", False)),
        )


def to_identifier(text: str) -> str:
    """Lower snake_case identifier for a free-text field or table name."""
    ident = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    if not ident:
        return "column"
    if ident[0].isdigit():
        ident = f"c_{ident}"
    return ident[:63]


# SQL spelled in upper case ("email VARCHAR(255) NOT NULL"): everything from the type on goes.
_SQL_TYPE_WORD = re.compile(
    r"\s+(?:INT|INTEGER|BIGINT|SMALLINT|VARCHAR|CHAR|TEXT|STRING|BOOL|BOOLEAN|DATE|DATETIME|TIMESTAMP|"
    r"DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|UUID|JSON|NOT\s+NULL|NULL|PRIMARY\s+KEY)\b.*$"
)
# A trailing lower-case type word ("name text", "age int") is a type, not part of the name.
_TRAILING_TYPE = re.compile(
    r"\s+(?:int|integer|bigint|smallint|varchar|char|text|string|str|bool|boolean|datetime|timestamp|"
    r"decimal|numeric|float|double|real|uuid|json)(?:\(\d+(?:,\s*\d+)?\))?$"
)
# "date"/"time" end natural names too ("signup date"), so they are a type only after a
# snake_case identifier ("signup_date date").
_TRAILING_DATE_AFTER_IDENT = re.compile(r"^([a-z0-9]+_[a-z0-9_]+)\s+(?:date|time)$")


def field_label(field: str) -> str:
    """The field's name without what a caller appended: a SQL type, a constraint or a note.

    "email VARCHAR(255)", "name text", "signup_date date", "name (string, required)" and
    "signup date: when they joined" all reduce to the name; "signup date" stays as it is.
    """
    text = re.split(r"[(:,;]|\s[-–—]\s", field.strip(), maxsplit=1)[0].strip()
    text = _SQL_TYPE_WORD.sub("", text).strip()
    text = _TRAILING_TYPE.sub("", text).strip()
    match = _TRAILING_DATE_AFTER_IDENT.match(text)
    if match:
        text = match.group(1)
    return text or field.strip()


def infer_column(field_name: str) -> Column:
    name = to_identifier(field_label(field_name))
    for pattern, col_type, nullable in _TYPE_RULES:
        if pattern.search(name):
            return Column(name=name, type=col_type, nullable=nullable)
    return Column(name=name, type="VARCHAR(255)", nullable=True)


def propose_columns(fields: Iterable[str]) -> list[Column]:
    """A primary key, one column per requested field, and audit timestamps."""
    columns: list[Column] = [Column(name="id", type="INTEGER", nullable=False, primary_key=True)]
    seen = {"id"}
    for field in fields:
        column = infer_column(field)
        if column.name in seen:
            continue
        seen.add(column.name)
        columns.append(column)
    if "created_at" not in seen:
        columns.append(Column(name="created_at", type="TIMESTAMP", nullable=False))
    return columns


def session_id_for(table_name: str, fields: Iterable[str]) -> str:
    """Stable id derived from the request, so the same request maps to the same session."""
    digest = hashlib.sha256(json.dumps([table_name, list(fields)]).encode()).hexdigest()
    return f"tbl-{digest[:10]}"


def validate(table_name: str, columns: list[Column]) -> list[str]:
    errors: list[str] = []
    if not _IDENTIFIER_RE.match(table_name):
        errors.append(
            f"Table name '{table_name}' must start with a letter or underscore and use only a-z, 0-9 and _."
        )
    if not columns:
        errors.append("Add at least one column.")
    names: set[str] = set()
    for index, column in enumerate(columns, start=1):
        if not _IDENTIFIER_RE.match(column.name):
            errors.append(f"Column {index} name '{column.name}' is not a valid identifier.")
        if column.name in names:
            errors.append(f"Column name '{column.name}' is used more than once.")
        names.add(column.name)
        if column.type not in COLUMN_TYPES:
            errors.append(f"Column '{column.name}' has unsupported type '{column.type}'.")
        if column.primary_key and column.nullable:
            errors.append(f"Primary key column '{column.name}' cannot be nullable.")
    if columns and not any(c.primary_key for c in columns):
        errors.append("Mark one column as the primary key.")
    return errors


def diff_columns(proposed: list[Column], edited: list[Column]) -> list[dict]:
    """Position-aware diff: a changed name at the same slot is a rename, not drop+add."""
    changes: list[dict] = []
    proposed_by_name = {c.name: c for c in proposed}
    edited_names = {c.name for c in edited}
    matched_proposed: set[str] = set()

    for index, column in enumerate(edited):
        if column.name in proposed_by_name:
            before = proposed_by_name[column.name]
            matched_proposed.add(before.name)
            if (before.type, before.nullable, before.primary_key) != (
                column.type,
                column.nullable,
                column.primary_key,
            ):
                changes.append(
                    {"kind": "modified", "column": column.name, "detail": _describe(before, column)}
                )
            continue
        slot = proposed[index] if index < len(proposed) else None
        if slot is not None and slot.name not in edited_names and slot.name not in matched_proposed:
            matched_proposed.add(slot.name)
            changes.append(
                {"kind": "renamed", "column": column.name, "detail": f"{slot.name} → {column.name}"}
            )
            continue
        changes.append({"kind": "added", "column": column.name, "detail": f"{column.type}"})

    for column in proposed:
        if column.name not in matched_proposed and column.name not in edited_names:
            changes.append({"kind": "removed", "column": column.name, "detail": column.type})
    return changes


def _describe(before: Column, after: Column) -> str:
    parts = []
    if before.type != after.type:
        parts.append(f"type {before.type} → {after.type}")
    if before.nullable != after.nullable:
        parts.append("now nullable" if after.nullable else "now NOT NULL")
    if before.primary_key != after.primary_key:
        parts.append("now primary key" if after.primary_key else "no longer primary key")
    return ", ".join(parts)


def render_ddl(table_name: str, columns: list[Column]) -> str:
    width = max((len(c.name) for c in columns), default=0)
    lines = []
    for column in columns:
        parts = [column.name.ljust(width), column.type]
        if not column.nullable:
            parts.append("NOT NULL")
        lines.append("  " + " ".join(parts))
    pk = [c.name for c in columns if c.primary_key]
    if pk:
        lines.append(f"  PRIMARY KEY ({', '.join(pk)})")
    return f"CREATE TABLE {table_name} (\n" + ",\n".join(lines) + "\n);"


def columns_as_dicts(columns: list[Column]) -> list[dict]:
    return [asdict(c) for c in columns]
