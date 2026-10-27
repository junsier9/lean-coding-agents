"""Stop queued native attempts after the parent confirms a provider limit.

Existing client checkpoints can still be graded. The stop file is created only
from an actual provider-limit observation, not from a forecast or test result.
"""
from pathlib import Path
import runner

STOP = Path(__file__).resolve().parents[2] / ".local/production-resource-stop.json"


def install():
    prepare = runner.prepare_workspace

    def guarded_prepare(*args, **kwargs):
        if STOP.exists():
            raise RuntimeError("Provider resource stop: no new native attempts; keep completed checkpoints")
        return prepare(*args, **kwargs)

    runner.prepare_workspace = guarded_prepare
