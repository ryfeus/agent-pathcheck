"""Named, versioned policy and invocation-local access to its parameters."""

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from .rules import Rule


@dataclass(frozen=True)
class Policy:
    name: str
    version: str
    build: Callable
    output_model: type[BaseModel] | None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    contract: str = ""
    required_requests: Callable[[Mapping[str, Any]], Sequence[BaseModel]] | None = None

    rules: Mapping[str, Rule] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        if any(key != rule.rule_id for key, rule in self.rules.items()):
            raise ValueError("Rule keys must match rule IDs")
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))

    @property
    def policy_hash(self):
        levels = "".join(f"\n{key}:{rule.level.value}" for key, rule in sorted(self.rules.items()))
        return sha256((self.version + "\n" + self.contract + levels).encode()).hexdigest()


_policy = ContextVar("agent_pathcheck_policy")


def current_policy() -> Policy:
    return _policy.get()
