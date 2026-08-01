#!/usr/bin/env python3
"""Replay Write/StrReplace tool calls from Cursor agent transcripts."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path("/MAX_BOT")
TRANSCRIPTS = [
    ROOT.parent
    / "root/.cursor/projects/MAX-BOT/agent-transcripts/09092851-86c2-4f16-bd1e-c05abd4a3d4e/09092851-86c2-4f16-bd1e-c05abd4a3d4e.jsonl",
    ROOT.parent
    / "root/.cursor/projects/MAX-BOT/agent-transcripts/a634a35f-ab25-4c73-8c65-bedbd2b18e45/a634a35f-ab25-4c73-8c65-bedbd2b18e45.jsonl",
]
# Fix paths when run on server
TRANSCRIPTS = [
    Path("/root/.cursor/projects/MAX-BOT/agent-transcripts/09092851-86c2-4f16-bd1e-c05abd4a3d4e/09092851-86c2-4f16-bd1e-c05abd4a3d4e.jsonl"),
    Path("/root/.cursor/projects/MAX-BOT/agent-transcripts/a634a35f-ab25-4c73-8c65-bedbd2b18e45/a634a35f-ab25-4c73-8c65-bedbd2b18e45.jsonl"),
]

TARGET_PREFIX = "/MAX_BOT/"
TAKSIMO_ONLY = True  # set False to recover all MAX_BOT files


@dataclass
class Op:
    seq: int
    transcript: str
    line: int
    kind: str
    path: str
    payload: dict


def iter_ops() -> list[Op]:
    ops: list[Op] = []
    seq = 0
    for tp in TRANSCRIPTS:
        if not tp.exists():
            print(f"WARN missing transcript: {tp}", file=sys.stderr)
            continue
        for line_no, line in enumerate(tp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "tool_use":
                    continue
                name = part.get("name", "")
                if name not in ("Write", "StrReplace"):
                    continue
                inp = part.get("input")
                if not isinstance(inp, dict):
                    continue
                path = inp.get("path", "")
                if not path.startswith(TARGET_PREFIX):
                    continue
                rel = path[len(TARGET_PREFIX) :]
                if TAKSIMO_ONLY and "taksimo" not in rel.lower() and rel not in (".env",):
                    continue
                ops.append(
                    Op(
                        seq=seq,
                        transcript=tp.stem[:8],
                        line=line_no,
                        kind=name,
                        path=path,
                        payload=inp,
                    )
                )
                seq += 1
    return ops


def apply_str_replace(content: str, old: str, new: str, replace_all: bool) -> str | None:
    if old not in content:
        return None
    if replace_all:
        return content.replace(old, new)
    return content.replace(old, new, 1)


def main() -> int:
    files: dict[str, str] = {}
    stats = {"write": 0, "replace_ok": 0, "replace_fail": 0, "written": 0}
    failed: list[str] = []

    for op in iter_ops():
        if op.kind == "Write":
            files[op.path] = op.payload.get("contents", "")
            stats["write"] += 1
            continue

        old = op.payload.get("old_string", "")
        new = op.payload.get("new_string", "")
        replace_all = bool(op.payload.get("replace_all", False))
        current = files.get(op.path)
        if current is None and Path(op.path).exists():
            current = Path(op.path).read_text(encoding="utf-8")
            files[op.path] = current
        if current is None:
            stats["replace_fail"] += 1
            failed.append(f"{op.transcript}:{op.line} {op.path} (no base)")
            continue
        updated = apply_str_replace(current, old, new, replace_all)
        if updated is None:
            stats["replace_fail"] += 1
            failed.append(f"{op.transcript}:{op.line} {op.path}")
            continue
        files[op.path] = updated
        stats["replace_ok"] += 1

    for path, content in sorted(files.items()):
        rel = Path(path)
        if not rel.is_absolute():
            continue
        rel.parent.mkdir(parents=True, exist_ok=True)
        rel.write_text(content, encoding="utf-8")
        stats["written"] += 1
        print(f"WROTE {path} ({len(content.splitlines())} lines)")

    print("\n--- stats ---")
    for k, v in stats.items():
        print(f"{k}: {v}")
    if failed:
        print(f"\nFailed patches ({len(failed)}):")
        for f in failed[:30]:
            print(" ", f)
        if len(failed) > 30:
            print(f"  ... and {len(failed) - 30} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
