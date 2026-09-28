"""Built-in analysis steps, in pipeline order.

To add a step: write a ``Step`` subclass in this package and insert an instance
after every step it lists in ``inputs``. To remove one: delete its entry (its
retained versions stay readable in artifact history). See docs/ANALYSIS-PIPELINE.md.
"""
from .census import CensusStep
from .directing import DirectingStep
from .discovery import DiscoveryStep
from .profiles import ProfilesStep
from .structure import StructureStep


def builtin_steps():
    return [StructureStep(), CensusStep(), DiscoveryStep(), ProfilesStep(), DirectingStep()]
