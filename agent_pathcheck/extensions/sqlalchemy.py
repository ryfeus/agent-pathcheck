"""Generate strict field boilerplate from SQLAlchemy Core, with explicit policy refinements."""

from typing import Literal
from pydantic import create_model
from agent_pathcheck.contracts import PolicyModel


def result_model(name, columns, *, refinements=None):
    """Accept Table.c or Select.selected_columns. Unknown types fail closed.

    Refinements map column names to (annotation, default/Field); constraints and
    semantic validators remain host-authored rather than inferred from data.
    """
    fields = {}
    for column in columns:
        try:
            annotation = column.type.python_type
        except (AttributeError, NotImplementedError) as exc:
            raise TypeError(f"No Python type for column {column.key}") from exc
        # Labels retain nullability on their underlying SQL expression.
        nullable = getattr(column, "nullable", getattr(getattr(column, "element", None), "nullable", None))
        if nullable is None:
            raise TypeError(f"Specify a typed column with known nullability: {column.key}")
        if nullable:
            annotation = annotation | None
        fields[column.key] = (annotation, ...)
    for key, refinement in (refinements or {}).items():
        if key not in fields:
            raise ValueError(f"Unknown column refinement: {key}")
        fields[key] = refinement
    return create_model(name, __base__=PolicyModel, **fields)


def request_model(name, columns, *, scope):
    fields = {}
    for column in columns:
        if column.key not in scope:
            raise ValueError(f"Missing scope for {column.key}")
        values = scope[column.key]
        values = values if isinstance(values, tuple) else (values,)
        if not values or any(type(value) is not column.type.python_type for value in values):
            raise ValueError(f"Scope must contain typed literal values for {column.key}")
        fields[column.key] = (Literal[values], ...)
    if set(scope) != set(fields):
        raise ValueError("Scope names must exactly match the request columns")
    return create_model(name, __base__=PolicyModel, **fields)
