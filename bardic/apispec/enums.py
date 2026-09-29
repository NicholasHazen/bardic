"""Named enumerations shared by several schemas.

A provider is declared once per domain, as a named schema, and every field of that domain refers to it. Where a
field genuinely carries values of more than one domain, it stays a plain string and its description says why.
Response enumerations are open sets for clients (see "Compatibility rules for clients"): adding a value is additive.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field
from typing_extensions import TypeAliasType

NarrationProvider = TypeAliasType('NarrationProvider', Annotated[Literal['system', 'gemini', 'breeze'], Field(
    description='A narration (speech) provider: `system` (macOS device voices, local), `gemini` (Google, cloud, billed) '
                'or `breeze` (a self-hosted server on the owner\'s network).')])

VoiceLibraryProvider = TypeAliasType('VoiceLibraryProvider', Annotated[Literal['breeze', 'gemini'], Field(
    description='A provider whose voices the voice library holds: `breeze` or `gemini`. Device (`system`) voices are '
                'not library voices.')])

AnalysisProvider = TypeAliasType('AnalysisProvider', Annotated[
    Literal['local', 'gemini', 'openai', 'anthropic', 'local_llm', 'booknlp', 'novel_analyzer'], Field(
        description='An analysis provider: `local` (built in, offline, free; the only provider of plain steps and of the '
                    'import draft), the cloud models `gemini`, `openai` and `anthropic` (billed, keyed), the self-hosted '
                    'model server `local_llm`, and the self-hosted chapter services `booknlp` and `novel_analyzer`.')])

CloudProvider = TypeAliasType('CloudProvider', Annotated[Literal['gemini', 'openai', 'anthropic'], Field(
    description='A cloud analysis provider that takes an API key: `gemini`, `openai` or `anthropic`.')])
