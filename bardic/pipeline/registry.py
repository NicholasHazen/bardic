"""An ordered, validated set of pipeline steps."""
from __future__ import annotations

import re

from .contract import GATES, LLM_PROVIDERS, METHODS, SCOPES, SERVICE_PROVIDERS, Step

STEP_ID = re.compile(r'[a-z][a-z0-9_]{1,39}')


class Registry:
    """Steps in dependency order. Validation fails loudly at construction.

    Declaration order is the display order; it must already list every input
    before the step that reads it, which keeps the graph acyclic and readable.
    """

    def __init__(self, steps):
        self._steps: dict[str, Step] = {}
        owners: dict[str, str] = {}
        for step in steps:
            if not isinstance(step, Step):
                raise TypeError('Pipeline steps must subclass bardic.pipeline.Step.')
            if not STEP_ID.fullmatch(step.id or ''):
                raise ValueError(f'Invalid pipeline step id: {step.id!r}')
            if step.id in self._steps:
                raise ValueError(f'Duplicate pipeline step id: {step.id}')
            if step.method not in METHODS or step.scope not in SCOPES or step.default_gate not in GATES:
                raise ValueError(f'Step {step.id} has an invalid method, scope or gate.')
            if step.default_model_role not in {'scan', 'analysis'}:
                raise ValueError(f'Step {step.id} has an invalid default model role.')
            if type(step.version) is not int or step.version < 1 or type(step.parallel) is not int or not 1 <= step.parallel <= 8:
                raise ValueError(f'Step {step.id} needs a positive integer version and 1–8 parallel units.')
            if step.request_version is not None and (type(step.request_version) is not int or not 1 <= step.request_version <= step.version):
                raise ValueError(f'Step {step.id} needs a request version from 1 to its version.')
            providers = step.allowed_providers()
            known = {'local', *LLM_PROVIDERS, *SERVICE_PROVIDERS}
            if not providers or len(set(providers)) != len(providers) or not set(providers) <= known or \
                    ('local' in providers) != (step.method == 'plain') or \
                    (step.method == 'service' and not set(providers) <= set(SERVICE_PROVIDERS)):
                raise ValueError(f'Step {step.id} declares invalid providers for its method.')
            if not set(step.offline_providers) <= set(providers) - {'local'}:
                raise ValueError(f'Step {step.id} lists offline providers it does not offer.')
            for dependency in step.inputs:
                if dependency not in self._steps:
                    raise ValueError(f'Step {step.id} reads {dependency}, which must be declared earlier.')
            for owned in step.owns:
                if owned in owners:
                    raise ValueError(f'Steps {owners[owned]} and {step.id} both claim {owned}.')
                owners[owned] = step.id
            self._steps[step.id] = step

    def __iter__(self):
        return iter(self._steps.values())

    def __contains__(self, step_id):
        return step_id in self._steps

    def get(self, step_id) -> Step:
        try:
            return self._steps[step_id]
        except KeyError:
            raise KeyError(f'Unknown pipeline step: {step_id}') from None

    def ids(self):
        return list(self._steps)

    def downstream(self, step_id):
        """Every step that transitively reads ``step_id``."""
        result, frontier = [], {step_id}
        for step in self._steps.values():
            if frontier.intersection(step.inputs):
                result.append(step.id)
                frontier.add(step.id)
        return result

    def closure(self, step_ids):
        """Requested steps in pipeline order, rejecting unknown IDs."""
        wanted = set(step_ids)
        for step_id in wanted:
            self.get(step_id)
        return [step for step in self._steps.values() if step.id in wanted]
