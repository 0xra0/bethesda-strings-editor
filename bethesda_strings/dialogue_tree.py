"""
Dialogue tree builder for Bethesda ESP/ESM files.

Performs a focused second-pass parse (QUST / DIAL / INFO records only) to
reconstruct the Quest → Topic → Response hierarchy needed by the visualiser.
Does not modify any data — read-only.

GRUP type 7 (Topic Children) provides the parent-DIAL FormID for INFO records.
DIAL.QNAM provides the parent-QUST FormID.
INFO.PNAM provides the predecessor INFO FormID (chain ordering).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


_FLAG_COMPRESSED = 0x00040000
_FLAG_LOCALIZED  = 0x00000080
_GRUP_TOPIC_CHILDREN = 7     # GRUP type whose label is parent DIAL FormID
_TARGET_SIGS = frozenset([b"QUST", b"DIAL", b"INFO"])


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class QuestNode:
    form_id: int
    edid: str
    name: str   # FULL display name (or EDID fallback)


@dataclass
class TopicNode:
    form_id: int
    edid: str
    name: str
    quest_form_id: int   # QNAM field — 0 if unlinked


@dataclass
class ResponseNode:
    form_id: int
    edid: str
    npc_line: str        # NAM1 — what the NPC says
    player_prompt: str   # RNAM — what the player sees in the choice menu
    prev_form_id: int    # PNAM — predecessor response FormID (0 = chain root)
    topic_form_id: int   # parent DIAL FormID (from containing GRUP type 7)


@dataclass
class DialogueTree:
    quests:           Dict[int, QuestNode]   = field(default_factory=dict)
    topics:           Dict[int, TopicNode]   = field(default_factory=dict)
    responses:        Dict[int, ResponseNode]= field(default_factory=dict)
    topic_responses:  Dict[int, List[int]]   = field(default_factory=dict)

    def ordered_quests(self) -> List[QuestNode]:
        return sorted(
            self.quests.values(),
            key=lambda q: (q.name or q.edid or "").lower(),
        )

    def quest_topics(self, quest_form_id: int) -> List[TopicNode]:
        return sorted(
            [t for t in self.topics.values() if t.quest_form_id == quest_form_id],
            key=lambda t: (t.name or t.edid or "").lower(),
        )

    def orphan_topics(self) -> List[TopicNode]:
        """Topics whose QNAM does not reference a known QUST."""
        return sorted(
            [t for t in self.topics.values() if t.quest_form_id not in self.quests],
            key=lambda t: (t.name or t.edid or "").lower(),
        )

    def topic_response_list(self, topic_form_id: int) -> List[ResponseNode]:
        ids = self.topic_responses.get(topic_form_id, [])
        return [self.responses[i] for i in ids if i in self.responses]


# ── Parser ────────────────────────────────────────────────────────────────────

def build_dialogue_tree(path: Path, encoding: str = "utf-8") -> DialogueTree:
    """Parse *path* and return the dialogue tree structure."""
    data = path.read_bytes()
    size = len(data)

    if size < 24 or data[0:4] != b"TES4":
        raise ValueError("Not a valid ESP/ESM file (missing TES4 header)")

    tes4_body    = struct.unpack_from("<I", data, 4)[0]
    tes4_flags   = struct.unpack_from("<I", data, 8)[0]
    is_localized = bool(tes4_flags & _FLAG_LOCALIZED)
    pos          = 24 + tes4_body

    tree = DialogueTree()
    grup_stack: list[tuple[int, int, int]] = []   # (end_pos, grup_type, label)

    while pos < size:
        while grup_stack and pos >= grup_stack[-1][0]:
            grup_stack.pop()

        if pos + 8 > size:
            break

        sig   = bytes(data[pos:pos + 4])
        dsize = struct.unpack_from("<I", data, pos + 4)[0]

        if sig == b"GRUP":
            if pos + 24 > size:
                break
            label     = struct.unpack_from("<I", data, pos + 8)[0]
            grup_type = struct.unpack_from("<I", data, pos + 12)[0]
            grup_stack.append((pos + dsize, grup_type, label))
            pos += 24
            continue

        rec_end = pos + 24 + dsize
        if rec_end > size:
            break

        if sig in _TARGET_SIGS:
            flags   = struct.unpack_from("<I", data, pos + 8)[0]
            form_id = struct.unpack_from("<I", data, pos + 12)[0]
            body    = bytes(data[pos + 24:rec_end])

            parent_dial = 0
            for (_, gt, lbl) in reversed(grup_stack):
                if gt == _GRUP_TOPIC_CHILDREN:
                    parent_dial = lbl
                    break

            _parse_record(sig, form_id, flags, body, encoding, is_localized,
                          tree, parent_dial)

        pos = rec_end

    _order_topic_responses(tree)
    return tree


def _parse_record(
    sig: bytes, form_id: int, flags: int, body: bytes,
    encoding: str, is_localized: bool,
    tree: DialogueTree, parent_dial: int,
) -> None:
    compressed = bool(flags & _FLAG_COMPRESSED)
    if compressed:
        if len(body) < 4:
            return
        try:
            body = zlib.decompress(body[4:])
        except zlib.error:
            return

    edid = ""
    full = ""
    qnam = 0
    pnam = 0
    nam1 = ""
    rnam = ""

    pos       = 0
    next_size = 0
    while pos < len(body):
        if pos + 6 > len(body):
            break
        fsig  = body[pos:pos + 4]
        fsize = struct.unpack_from("<H", body, pos + 4)[0]
        pos  += 6
        actual    = next_size if next_size else fsize
        next_size = 0
        if pos + actual > len(body):
            break
        fdata = body[pos:pos + actual]
        pos  += actual

        if fsig == b"XXXX":
            if len(fdata) >= 4:
                next_size = struct.unpack_from("<I", fdata)[0]
        elif fsig == b"EDID":
            edid = fdata.rstrip(b"\x00").decode("ascii", errors="replace")
        elif fsig == b"FULL":
            full = _decode_text(fdata, encoding, is_localized)
        elif fsig == b"QNAM" and len(fdata) >= 4:
            qnam = struct.unpack_from("<I", fdata)[0]
        elif fsig == b"PNAM" and len(fdata) >= 4:
            pnam = struct.unpack_from("<I", fdata)[0]
        elif fsig == b"NAM1":
            nam1 = _decode_text(fdata, encoding, is_localized)
        elif fsig == b"RNAM":
            rnam = _decode_text(fdata, encoding, is_localized)

    label = full or edid or f"0x{form_id:08X}"

    if sig == b"QUST":
        tree.quests[form_id] = QuestNode(form_id=form_id, edid=edid, name=label)
    elif sig == b"DIAL":
        tree.topics[form_id] = TopicNode(
            form_id=form_id, edid=edid, name=label, quest_form_id=qnam,
        )
    elif sig == b"INFO":
        tree.responses[form_id] = ResponseNode(
            form_id=form_id, edid=edid,
            npc_line=nam1, player_prompt=rnam,
            prev_form_id=pnam, topic_form_id=parent_dial,
        )
        if parent_dial not in tree.topic_responses:
            tree.topic_responses[parent_dial] = []
        tree.topic_responses[parent_dial].append(form_id)


def _decode_text(fdata: bytes, encoding: str, is_localized: bool) -> str:
    if not fdata:
        return ""
    if is_localized and len(fdata) == 4:
        sid = struct.unpack_from("<I", fdata)[0]
        return f"[StringID:{sid:08X}]"
    raw = fdata.rstrip(b"\x00")
    if not raw:
        return ""
    try:
        return raw.decode(encoding, errors="replace")
    except Exception:
        return raw.decode("latin-1", errors="replace")


def _order_topic_responses(tree: DialogueTree) -> None:
    """Sort each topic's response list by PNAM chain order (roots first)."""
    for topic_fid, resp_ids in tree.topic_responses.items():
        resp_set = set(resp_ids)
        resp_map = {fid: tree.responses[fid] for fid in resp_ids if fid in tree.responses}

        # For each node: which node it points to as its single "main" successor
        # (first child found wins; extra children become new chain roots)
        successor:   dict[int, int]  = {}
        extra_roots: list[int]       = []
        for fid in resp_ids:
            r = resp_map.get(fid)
            if r and r.prev_form_id in resp_set:
                if r.prev_form_id not in successor:
                    successor[r.prev_form_id] = fid
                else:
                    extra_roots.append(fid)

        roots = [
            fid for fid in resp_ids
            if fid in resp_map and resp_map[fid].prev_form_id not in resp_set
        ]

        ordered: list[int] = []
        placed:  set[int]  = set()

        def follow(fid: int) -> None:
            if fid in placed or fid not in resp_set:
                return
            placed.add(fid)
            ordered.append(fid)
            nxt = successor.get(fid, 0)
            if nxt:
                follow(nxt)

        for root in roots:
            follow(root)
        for root in extra_roots:
            if root not in placed:
                follow(root)
        for fid in resp_ids:
            if fid not in placed:
                ordered.append(fid)

        tree.topic_responses[topic_fid] = ordered
