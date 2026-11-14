"""Render the registered interface and actual validators, never a second policy."""

from dataclasses import dataclass
import inspect
from textwrap import dedent
from typing import Any, Literal, Union, get_args, get_origin
from types import UnionType

from pydantic import BaseModel

from .contracts import PolicyModel


@dataclass(frozen=True)
class Input:
    annotation: Any
    value: Any


def type_source(annotation):
    origin = get_origin(annotation)
    if annotation is type(None):
        return "None"
    if origin in (Union, UnionType):
        return " | ".join(type_source(v) for v in get_args(annotation))
    if origin is Literal:
        return f"Literal[{', '.join(repr(v) for v in get_args(annotation))}]"
    if origin:
        args = ", ".join(type_source(v) for v in get_args(annotation))
        return f"{getattr(origin, '__name__', str(origin))}[{args}]"
    if annotation is Ellipsis:
        return "..."
    return getattr(annotation, "__name__", str(annotation).removeprefix("typing."))


def model_source(model):
    try:
        return dedent(inspect.getsource(model)).strip()
    except (OSError, TypeError):
        bases = ", ".join(base.__name__ for base in model.__bases__)
        lines = [f"class {model.__name__}({bases}):"]
        for name, field in model.model_fields.items():
            annotation = type_source(field.annotation)
            options = {}
            for item in field.metadata:
                for key in ("ge", "gt", "le", "lt", "min_length", "max_length", "allow_inf_nan"):
                    if hasattr(item, key) and getattr(item, key) is not None:
                        options[key] = getattr(item, key)
            if field.alias:
                options["alias"] = field.alias
            default = "" if field.is_required() else f" = {field.default!r}"
            if options:
                values = ["..." if field.is_required() else repr(field.default)]
                values.extend(f"{key}={value!r}" for key, value in options.items())
                default = " = Field(" + ", ".join(values) + ")"
            lines.append(f"    {name}: {annotation}{default}")
        return "\n".join(lines if len(lines) > 1 else lines + ["    pass"])


def render_contract(operations, constructors, inputs, extra_sources=()):
    constructors = dict(constructors)
    models = [PolicyModel]
    for operation in operations:
        if operation.request_type not in constructors.values():
            raise ValueError("Operation request constructor must be exposed")
        models.extend((operation.request_type, operation.result_type))
    models.extend(constructors.values())
    lines = ["from typing import Literal", "from pydantic import BaseModel, ConfigDict, Field, model_validator",
             "from agent_pathcheck import current_evidence, current_policy, PolicyViolation", ""]
    for name, value in inputs.items():
        item = value if isinstance(value, Input) else Input(type(value), value)
        lines.append(f"{name}: {type_source(item.annotation)} = {item.value!r}")
    lines.append("")
    seen = set()
    for source in [*extra_sources, *models]:
        if source in seen:
            continue
        seen.add(source)
        lines.append(model_source(source) if isinstance(source, type) and issubclass(source, BaseModel)
                     else dedent(inspect.getsource(source)).strip())
        lines.append("")
    for name, model in constructors.items():
        if name != model.__name__:
            lines.append(f"{name} = {model.__name__}")
    lines.append("# Guest constructors: " + ", ".join(constructors))
    for op in operations:
        result = op.result_type.__name__
        lines.append(f"def query(request: {op.request_type.__name__}) -> "
                     f"{f'list[{result}]' if op.many else result}: ...")
    return "\n".join(lines)


def tool_description(prose, contract, *, limits=None, adapter_limits=None, max_source_chars=None):
    from .runtime import MAX_SOURCE_CHARS, MONTY_LIMITS
    from .report import ExecutionReport
    effective = {"max_source_chars": MAX_SOURCE_CHARS if max_source_chars is None else max_source_chars,
                 **MONTY_LIMITS, **(limits or {}), **(adapter_limits or {})}
    budgets = "\n".join(f"- {name}: {value}" for name, value in effective.items())
    glossary = "\n".join(f"- {name}: {field.description}" for name, field in ExecutionReport.model_fields.items()
                         if field.description)
    return (f"{prose}\nConstructors, inputs and query are supplied; do not import or redefine them. "
            "Each invocation uses fresh evidence and a fresh guest session. Returned rows have attributes.\n"
            f"```python\n{contract}\n```\nExecution limits:\n{budgets}\n"
            f"Report fields:\n{glossary}\n"
            "validation_status and validated_answer describe the checked answer; model prose is unverified. "
            "answer_check is an independent fixture grade. queries contains SQL, parameters and rows "
            "when the SQLite adapter is used. Use errors to repair the submitted code.")
