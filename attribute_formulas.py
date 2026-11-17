"""Formula evaluation for computed document attributes.

A computed attribute's schema entry carries a `formula` dict describing
one operation over one or two operands, where each operand is either
another attribute (by name) or a typed literal:

    {"op": "add",
     "left": {"kind": "attr", "value": "Idade"},
     "right": {"kind": "literal", "value": "1"}}

There is deliberately no free-text expression / eval() here: OPERATIONS
is a small fixed whitelist, so a formula can never do anything beyond
what's listed below, and evaluate() re-derives the value from the
document's current attributes every time rather than storing (and
risking staling) a result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

ERROR_VALUE = "#ERROR"


@dataclass(frozen=True)
class Operation:
    key: str
    label: str
    arity: int
    domain: str  # "integer" | "string" | "date" -- the type operands must be
    fn: Callable


OPERATIONS: dict[str, Operation] = {
    op.key: op
    for op in [
        Operation("add", "Add (+)", 2, "integer", lambda a, b: a + b),
        Operation("subtract", "Subtract (-)", 2, "integer", lambda a, b: a - b),
        Operation("multiply", "Multiply (×)", 2, "integer", lambda a, b: a * b),
        Operation("divide", "Divide (÷)", 2, "integer", lambda a, b: round(a / b, 4)),
        Operation("concat", "Concatenate (+)", 2, "string", lambda a, b: f"{a}{b}"),
        Operation("uppercase", "Uppercase", 1, "string", lambda a: a.upper()),
        Operation("lowercase", "Lowercase", 1, "string", lambda a: a.lower()),
        Operation("days_between", "Days Between", 2, "date", lambda a, b: (a - b).days),
    ]
}


def _parse_operand(raw: str, domain: str):
    if domain == "integer":
        return int(raw)
    if domain == "date":
        return date.fromisoformat(raw)
    return raw


def _resolve_operand(operand: dict, doc: dict, schema_by_name: dict[str, dict]) -> str:
    if operand["kind"] == "literal":
        return operand["value"]

    name = operand["value"]
    entry = schema_by_name.get(name)
    if entry is None:
        return ""
    if entry.get("type") == "computed":
        raise ValueError("A computed attribute can't use another computed attribute as an operand")
    return doc.get("attributes", {}).get(name, "")


def evaluate(formula: dict, doc: dict, schema_by_name: dict[str, dict]) -> str:
    """Compute a computed attribute's current value for one document.

    Returns ERROR_VALUE instead of raising when an operand is missing or
    doesn't parse as the operation's expected type, since this runs every
    time the value is displayed or exported and should never crash the
    caller over one bad/missing source value.
    """
    op = OPERATIONS[formula["op"]]
    try:
        left = _parse_operand(_resolve_operand(formula["left"], doc, schema_by_name), op.domain)
        if op.arity == 1:
            result = op.fn(left)
        else:
            right = _parse_operand(_resolve_operand(formula["right"], doc, schema_by_name), op.domain)
            if op.key == "divide" and right == 0:
                return ERROR_VALUE
            result = op.fn(left, right)
    except (ValueError, TypeError, KeyError):
        return ERROR_VALUE

    return str(result)
