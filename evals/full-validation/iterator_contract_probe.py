"""Descriptive follow-up to blind pair 01; never changes frozen acceptance."""
import argparse
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--project", type=Path, required=True)
args = parser.parse_args()
project = args.project.resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(project))
import more_itertools as m
assert Path(m.__file__).resolve().is_relative_to(project)

seen = []
def source():
    for value in (1, 2):
        seen.append(value)
        yield value

gray_result = list(m.gray_product(source(), repeat=0))

class TailError:
    def __iter__(self):
        yield "a"
        raise RuntimeError("tail")

generator = m.partial_product(TailError(), repeat=2)
def advance():
    try:
        return {"kind": "value", "value": next(generator)}
    except Exception as exc:
        return {"kind": "exception", "type": type(exc).__name__, "message": str(exc)}

first = advance()
print(json.dumps({"module_file": m.__file__, "gray_result": gray_result,
                  "gray_repeat_zero_consumed": seen, "partial_first": first,
                  "partial_second": advance() if first["kind"] == "value" else None}))
