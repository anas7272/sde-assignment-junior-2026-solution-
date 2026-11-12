"""Workflow lead schemas and the disposition catalogue.

The loader is deliberately tolerant about the JSON shape (``fields`` or
``columns``; a list of field objects or a ``{name: spec}`` mapping) because the
schema files are an input we do not control.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import normalize as nz

FIELD_TYPES = {"string", "phone", "date", "decimal", "enum"}
_TRUE = {"true", "1", "yes", "y", "t"}


@dataclass(frozen=True)
class Field:
    name: str
    type: str = "string"
    required: bool = False
    aliases: Tuple[str, ...] = ()
    pii: bool = False
    reference: bool = False
    values: Tuple[str, ...] = ()
    default: Optional[object] = None


@dataclass
class Schema:
    workflow_id: str
    fields: List[Field]
    raw: dict = field(default_factory=dict)

    @property
    def phone_field(self) -> Field:
        """The identity phone: the first field of type ``phone``."""
        return next(f for f in self.fields if f.type == "phone")

    @property
    def reference_field(self) -> Optional[Field]:
        return next((f for f in self.fields if f.reference), None)

    def header_lookup(self) -> Dict[str, Field]:
        """normalised header text -> Field (first field in schema order wins)."""
        lookup: Dict[str, Field] = {}
        for f in self.fields:
            for key in (f.name, *f.aliases):
                lookup.setdefault(nz.normalize_header(key), f)
        return lookup


def _truthy(v: object) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in _TRUE
    return bool(v)


def _parse_field(spec: dict, fallback_name: Optional[str] = None) -> Field:
    name = spec.get("name") or fallback_name
    if not name:
        raise ValueError(f"schema field without a name: {spec!r}")
    ftype = str(spec.get("type", "string")).lower()
    if ftype not in FIELD_TYPES:
        ftype = "string"
    values = spec.get("values") or spec.get("enum") or ()
    default = spec.get("default", spec.get("default_value"))
    return Field(
        name=name, type=ftype, required=_truthy(spec.get("required", False)),
        aliases=tuple(str(a) for a in (spec.get("aliases") or ())),
        pii=_truthy(spec.get("pii", False)), reference=_truthy(spec.get("reference", False)),
        values=tuple(str(v) for v in values), default=default,
    )


def load_schema(path: Path) -> Schema:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    wf = (data.get("workflow_id") or data.get("workflow") or data.get("id") or path.stem)
    raw_fields = data.get("fields") or data.get("columns") or []
    if isinstance(raw_fields, dict):
        fields = [_parse_field(spec if isinstance(spec, dict) else {}, name)
                  for name, spec in raw_fields.items()]
    else:
        fields = [_parse_field(spec) for spec in raw_fields]
    schema = Schema(workflow_id=str(wf), fields=fields, raw=data)
    if not any(f.type == "phone" for f in fields):
        raise ValueError(f"{path.name}: schema has no phone field, so leads have no identity")
    return schema


def load_schemas(directory: Path) -> Dict[str, Schema]:
    """workflow_id -> Schema. Also indexed by file stem so directory names resolve."""
    out: Dict[str, Schema] = {}
    for p in sorted(directory.glob("*.json")):
        s = load_schema(p)
        out[s.workflow_id] = s
        out.setdefault(p.stem, s)
    return out


# --------------------------------------------------------------- dispositions
def _pick(row: dict, *names: str) -> Optional[str]:
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None


def load_dispositions(path: Path) -> List[Tuple[Optional[str], str, bool, bool]]:
    """-> [(workflow_id or None for "all workflows", code, is_terminal, is_success)]."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            code = _pick(row, "disposition_code", "code", "disposition")
            if not code:
                continue
            wf = _pick(row, "workflow_id", "workflow") or None
            out.append((wf, code,
                        _truthy(_pick(row, "is_terminal", "terminal") or ""),
                        _truthy(_pick(row, "is_success", "success") or "")))
    return out
