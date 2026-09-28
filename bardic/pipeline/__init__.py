"""Step-based text analysis with versioned, reviewable outputs.

Public surface: the step contract (``Step``, ``Unit``, ``LLMRequest``,
``Conflict``, ``StepContext``, ``locked``), ``Registry`` and ``default_registry``.
"""
from .contract import Conflict, LLMRequest, Step, StepContext, Unit, locked
from .registry import Registry


def default_registry():
    from .steps import builtin_steps
    return Registry(builtin_steps())


__all__ = ['Conflict', 'LLMRequest', 'Registry', 'Step', 'StepContext', 'Unit', 'default_registry', 'locked']
