"""Invocation-local, whole-batch evidence. Never exposed as a guest binding."""

from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from pydantic import BaseModel

from .violations import PolicyViolation


class Evidence:
    def __init__(self):
        self._completed = {}
        self._blocks = []

    @staticmethod
    def _key(request):
        # Frozen Pydantic models can still contain unhashable lists/dicts. A
        # canonical snapshot also prevents later host mutation changing a key.
        # Preserve Python types: JSON would conflate {1: ...} with {"1": ...}.
        return _freeze(request)

    def completed(self, request):
        return self._key(request) in self._completed

    def results(self, request):
        if not self.completed(request):
            raise KeyError(f"{type(request).__name__} has not completed")
        return deepcopy(self._completed[self._key(request)][1])

    def find(self, request_type, **fields):
        return [(deepcopy(request), deepcopy(rows)) for request, rows, _ in self._completed.values()
                if type(request) is request_type
                and all(getattr(request, key) == value for key, value in fields.items())]

    def require(self, request):
        if not self.completed(request):
            raise PolicyViolation("prerequisite", "evidence.required",
                                  f"Complete {type(request).__name__} before using its evidence",
                                  details={"request": request.model_dump(mode="json")})
        return self.results(request)

    def caveats(self, request):
        if not self.completed(request):
            raise KeyError(f"{type(request).__name__} has not completed")
        return deepcopy(self._completed[self._key(request)][2])

    def all_caveats(self):
        by_value = {(c.rule_id, c.text): c for _, _, caveats in self._completed.values() for c in caveats}
        return deepcopy([by_value[key] for key in sorted(by_value)])

    def _commit(self, request, values, caveats=()):
        self._completed[self._key(request)] = deepcopy((request, values, list(caveats)))

    def _block(self, request, kind, rule_id, *, required):
        if required:
            self._blocks.append((deepcopy(request), kind, rule_id))

    def justifies(self, kind, rule_id, required_requests=()):
        required = {self._key(request) for request in required_requests}
        return any(k == kind and rule == rule_id and self._key(request) in required
                   and not self.completed(request)
                   for request, k, rule in self._blocks)


def _freeze(value):
    if isinstance(value, BaseModel):
        return type(value), tuple((name, _freeze(getattr(value, name))) for name in type(value).model_fields)
    if isinstance(value, dict):
        return dict, frozenset((_freeze(key), _freeze(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return type(value), tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return type(value), frozenset(_freeze(item) for item in value)
    hash(value)  # Fail explicitly for unsupported mutable host objects.
    return type(value), value


_evidence = ContextVar("agent_pathcheck_evidence")


def current_evidence() -> Evidence:
    return _evidence.get()


@contextmanager
def evidence_context(ledger, policy):
    """Bind trusted state for execution or a host-side evidence check."""
    from .policy import _policy
    evidence_token, policy_token = _evidence.set(ledger), _policy.set(policy)
    try:
        yield
    finally:
        _policy.reset(policy_token)
        _evidence.reset(evidence_token)
