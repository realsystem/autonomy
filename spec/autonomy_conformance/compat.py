"""Backward-compatibility checker between two releases of the same MAJOR.

Applies spec/README.md#versioning (design/component-contracts.md §14):
adding an optional property or a new schema is allowed; removing or renaming
a property, changing a type, adding a value to a closed enum, or tightening
validation is breaking. The checker is conservative: a change it does not
understand is reported as breaking, to be resolved by a MAJOR bump or an
explicit review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ANNOTATIONS = {"title", "description", "$comment", "examples", "deprecated", "readOnly", "writeOnly", "x-autonomy-open-enum"}
LOWER_BOUNDS = {"minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties", "minContains"}
UPPER_BOUNDS = {"maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties", "maxContains"}
SINGLE = {"items", "propertyNames", "contains", "not", "if", "then", "else", "unevaluatedItems", "unevaluatedProperties"}
ARRAYS = {"allOf", "anyOf", "oneOf", "prefixItems"}
MAPS = {"properties", "$defs", "patternProperties", "dependentSchemas"}


@dataclass
class Change:
    breaking: bool
    where: str
    message: str

    def __str__(self) -> str:
        return f"{'BREAKING' if self.breaking else 'ok      '} {self.where}: {self.message}"


def _types(s: dict) -> set[str] | None:
    t = s.get("type")
    if t is None:
        return None
    return {t} if isinstance(t, str) else set(t)


def compare(old: Any, new: Any, where: str) -> list[Change]:
    out: list[Change] = []
    if isinstance(old, bool) or isinstance(new, bool):
        if old != new:
            loosened = new is True or (old is False and isinstance(new, dict))
            out.append(Change(not loosened, where, f"boolean schema changed {old!r} → {new!r}"))
        return out
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            out.append(Change(True, where, "schema replaced"))
        return out

    for kw in sorted(set(old) | set(new)):
        if kw in ANNOTATIONS:
            continue
        a, b = old.get(kw), new.get(kw)
        here = f"{where}/{kw}"
        if kw not in new:
            if kw in UPPER_BOUNDS | LOWER_BOUNDS:
                out.append(Change(False, here, "bound removed (loosened)"))
            elif kw == "additionalProperties":
                out.append(Change(False, here, "additionalProperties removed (loosened)"))
            elif kw == "required":
                out.append(Change(True, here, f"no longer required: {sorted(a)}"))
            elif kw == "default":
                out.append(Change(False, here, "default removed"))
            else:
                out.append(Change(True, here, f"keyword {kw} removed"))
            continue
        if kw not in old:
            if kw == "properties":
                for p in b:
                    out.append(Change(False, f"{here}/{p}", "property added"))
            elif kw == "additionalProperties" and b is True:
                pass
            elif kw == "default":
                out.append(Change(False, here, "default added"))
            elif kw == "required":
                out.append(Change(True, here, f"newly required: {sorted(b)}"))
            else:
                out.append(Change(True, here, f"keyword {kw} added (tightens validation)"))
            continue
        if a == b:
            continue
        if kw in MAPS:
            for k in sorted(set(a) | set(b)):
                if k not in b:
                    out.append(Change(True, f"{here}/{k}", "removed"))
                elif k not in a:
                    out.append(Change(kw != "properties" and kw != "$defs", f"{here}/{k}", "added"))
                else:
                    out.extend(compare(a[k], b[k], f"{here}/{k}"))
        elif kw in SINGLE or (kw == "additionalProperties" and isinstance(a, dict) and isinstance(b, dict)):
            out.extend(compare(a, b, here))
        elif kw == "additionalProperties":
            if b is True or (a is False and isinstance(b, dict)):
                out.append(Change(False, here, f"loosened {a!r} → {'schema' if isinstance(b, dict) else b!r}"))
            else:
                out.append(Change(True, here, "tightened"))
        elif kw in ARRAYS:
            if len(a) != len(b):
                out.append(Change(True, here, f"{len(a)} → {len(b)} subschemas (review manually)"))
            else:
                for i, (x, y) in enumerate(zip(a, b)):
                    out.extend(compare(x, y, f"{here}/{i}"))
        elif kw == "required":
            if set(b) - set(a):
                out.append(Change(True, here, f"newly required: {sorted(set(b) - set(a))}"))
            if set(a) - set(b):
                out.append(Change(True, here, f"no longer required: {sorted(set(a) - set(b))}"))
        elif kw == "enum":
            added, removed = [v for v in b if v not in a], [v for v in a if v not in b]
            if removed:
                out.append(Change(True, here, f"values removed: {removed}"))
            if added:
                open_enum = bool(old.get("x-autonomy-open-enum"))
                out.append(Change(not open_enum, here, f"values added: {added}" + (" (open enum)" if open_enum else " (consumers must handle them)")))
        elif kw == "type":
            out.append(Change(True, here, f"type changed {_types(old)} → {_types(new)}"))
        elif kw in LOWER_BOUNDS:
            out.append(Change(b > a, here, f"{a} → {b}" + (" (tightened)" if b > a else " (loosened)")))
        elif kw in UPPER_BOUNDS:
            out.append(Change(b < a, here, f"{a} → {b}" + (" (tightened)" if b < a else " (loosened)")))
        elif kw == "default":
            out.append(Change(False, here, f"default {a!r} → {b!r}"))
        elif kw == "uniqueItems":
            out.append(Change(bool(b), here, f"{a} → {b}"))
        else:
            out.append(Change(True, here, f"{kw} changed"))
    return out


def compare_dirs(old_dir: Path, new_dir: Path) -> list[Change]:
    out: list[Change] = []
    old_files = {p.name: p for p in old_dir.glob("*.schema.json")}
    new_files = {p.name: p for p in new_dir.glob("*.schema.json")}
    for name in sorted(old_files.keys() - new_files.keys()):
        out.append(Change(True, name, "schema removed"))
    for name in sorted(new_files.keys() - old_files.keys()):
        out.append(Change(False, name, "schema added"))
    for name in sorted(old_files.keys() & new_files.keys()):
        a = json.loads(old_files[name].read_text(encoding="utf-8"))
        b = json.loads(new_files[name].read_text(encoding="utf-8"))
        out.extend(compare(a, b, name + "#"))
    return out
