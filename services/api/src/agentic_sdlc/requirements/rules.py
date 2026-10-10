"""Deterministic requirement extraction over normalized blocks (the `heuristic` strategy).

Pure functions: blocks in, extraction dict out. Every item quotes the block it came from, so the
same locator validation applies to heuristic and model output. The rules are deliberately simple
and transparent; their quality is measured by `evals/requirements`.
"""

from __future__ import annotations

import re
from typing import Any

ID_RE = re.compile(
    r"^\s*(?P<id>(?P<prefix>FR|NFR|BR|REQ|US|SR|CON)[-_ ]?(?P<num>\d{1,4}))\b[\s:.)\]\u2013\u2014-]*",
    re.IGNORECASE,
)
MODAL_RE = re.compile(
    r"\b(shall|must|is required to|are required to|should|will allow|will be able to|needs? to|"
    r"has to|have to)\b",
    re.IGNORECASE,
)
TRANSCRIPT_NEED_RE = re.compile(
    r"\b(we need|i need|users? need|customers? need|it needs to|has to|have to|must|should be able to|"
    r"we want|i want|it would be great if|it'd be great if|we agreed|we decided|let's go with)\b",
    re.IGNORECASE,
)
RULE_RE = re.compile(
    r"(^\s*(if|when|whenever)\b.+\b(then|must|shall|is|are|will)\b)|(\bonly\b.+\b(if|when|after|before|unless)\b)",
    re.IGNORECASE,
)
GWT_RE = re.compile(r"^\s*(given\b.+\bwhen\b.+\bthen\b|given\b|when\b.+\bthen\b)", re.IGNORECASE)
AC_PREFIX_RE = re.compile(r"^\s*(AC[-\s]?\d*|acceptance criterion)\s*[:.)-]\s*", re.IGNORECASE)
TBD_RE = re.compile(r"\b(TBD|TBC|to be (decided|confirmed|determined|defined))\b", re.IGNORECASE)
INJECTION_RE = re.compile(
    r"(ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|prompts?))|"
    r"(disregard (the |all |any )?(previous|prior|above|system) )|(you are now\b)|(system prompt)|"
    r"(as an ai (model|assistant))|(approve (this|the) (release|architecture|change)s? (automatically|now))|"
    r"(do not (ask|request) (for )?(human )?approval)|(bypass (the )?(approval|review|tests?))",
    re.IGNORECASE,
)
VAGUE_TERMS = [
    "fast",
    "quickly",
    "quick",
    "user-friendly",
    "user friendly",
    "easy",
    "easily",
    "intuitive",
    "simple",
    "robust",
    "scalable",
    "flexible",
    "efficient",
    "seamless",
    "seamlessly",
    "appropriate",
    "appropriately",
    "as needed",
    "as required",
    "etc",
    "and so on",
    "approximately",
    "minimal",
    "reasonable",
    "adequate",
    "state-of-the-art",
    "modern",
    "high performance",
    "responsive",
    "secure",
    "reliable",
    "timely",
    "several",
    "many",
    "large number",
]
VAGUE_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in VAGUE_TERMS) + r")\b", re.IGNORECASE)
NFR_HINT_RE = re.compile(
    r"\b(within \d|response time|latency|availability|uptime|throughput|concurren|encrypt|accessib|wcag|"
    r"performance|p9[59]|percent|%|audit log|retention|backup|recover|security|compliance)\b",
    re.IGNORECASE,
)
QTY_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>characters?|chars?|seconds?|secs?|ms|milliseconds?|minutes?|mins?|"
    r"hours?|days?|items?|tasks?|users?|attempts?|%|percent|mb|gb|kb)\b",
    re.IGNORECASE,
)
UNIT_CANON = {
    "character": "char",
    "characters": "char",
    "char": "char",
    "chars": "char",
    "second": "s",
    "seconds": "s",
    "sec": "s",
    "secs": "s",
    "ms": "ms",
    "millisecond": "ms",
    "milliseconds": "ms",
    "minute": "min",
    "minutes": "min",
    "min": "min",
    "mins": "min",
    "hour": "h",
    "hours": "h",
    "day": "d",
    "days": "d",
    "item": "item",
    "items": "item",
    "task": "task",
    "tasks": "task",
    "user": "user",
    "users": "user",
    "attempt": "attempt",
    "attempts": "attempt",
    "%": "%",
    "percent": "%",
    "mb": "mb",
    "gb": "gb",
    "kb": "kb",
}
STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "be",
    "is",
    "are",
    "and",
    "or",
    "than",
    "for",
    "with",
    "that",
    "this",
    "system",
    "shall",
    "must",
    "should",
    "will",
    "more",
    "less",
    "longer",
    "shorter",
    "most",
    "least",
    "at",
    "in",
    "on",
    "by",
    "up",
    "no",
    "not",
    "each",
    "per",
    "any",
    "all",
    "within",
    "under",
    "over",
    "have",
    "has",
    "can",
    "may",
    "maximum",
    "minimum",
    "max",
    "min",
    "limit",
    "allowed",
    "reject",
    "rejects",
    "accept",
    "accepts",
    "exceed",
    "exceeds",
    "only",
}
TITLE_TRAILING = {
    "with",
    "and",
    "or",
    "of",
    "to",
    "for",
    "the",
    "a",
    "an",
    "in",
    "on",
    "by",
    "see",
    "from",
    "that",
    "than",
    "before",
    "after",
    "when",
    "if",
}
PRIORITY_MAP = {
    "must": "must",
    "shall": "must",
    "high": "must",
    "critical": "must",
    "p1": "must",
    "mandatory": "must",
    "should": "should",
    "medium": "should",
    "p2": "should",
    "could": "could",
    "may": "could",
    "low": "could",
    "p3": "could",
    "nice to have": "could",
    "won't": "could",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_id(text: str) -> tuple[str | None, str | None, str]:
    m = ID_RE.match(text)
    if not m:
        return None, None, text.strip()
    prefix = m.group("prefix").upper()
    key = f"{prefix}-{int(m.group('num')):03d}"
    return key, prefix, text[m.end() :].strip()


def _title(statement: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit[:120]
    head = statement.split(":", 1)
    if len(head) == 2 and 3 <= len(head[0]) <= 80:
        return head[0].strip()
    words = re.sub(
        r"^(the system|the application|the service|users?|the user)\s+(shall|must|should|will)\s+",
        "",
        statement,
        flags=re.IGNORECASE,
    ).split()
    picked = words[:9]
    while picked and (picked[-1].lower().strip("(,.;:") in TITLE_TRAILING or picked[-1].startswith("(")):
        picked.pop()
    title = " ".join(picked).rstrip(".,;:")
    return (title[:1].upper() + title[1:]) if title else statement[:60]


def _priority(text: str, override: str | None = None) -> str:
    if override:
        key = override.strip().lower()
        for k, v in PRIORITY_MAP.items():
            if key.startswith(k):
                return v
    m = re.search(r"\b(shall|must|should|could|may)\b", text, re.IGNORECASE)
    return PRIORITY_MAP.get(m.group(1).lower(), "should") if m else "should"


def _kind(prefix: str | None, text: str) -> str:
    if prefix == "NFR":
        return "non_functional"
    if prefix == "CON":
        return "constraint"
    if prefix in ("FR", "US", "REQ", "SR"):
        return "functional"
    return "non_functional" if NFR_HINT_RE.search(text) else "functional"


def _third_person(verb: str) -> str:
    v = verb.lower()
    if v in ("be",):
        return "is"
    if v == "have":
        return "has"
    if re.search(r"(s|sh|ch|x|z|o)$", v):
        return verb + "es"
    if re.search(r"[^aeiou]y$", v):
        return verb[:-1] + "ies"
    return verb + "s"


def inferred_criterion(statement: str) -> str:
    """Turn 'The system shall reject X' into the observable outcome 'The system rejects X'."""
    m = re.match(
        r"^(?P<subj>(the )?[\w\s'-]{1,40}?)\s+(shall|must|should|will|is required to|needs to)\s+"
        r"(?P<verb>\w+)\s*(?P<rest>.*)$",
        statement,
        re.IGNORECASE,
    )
    if not m:
        return f"It can be demonstrated that: {statement.rstrip('.')}."
    subj = m.group("subj").strip()
    subj = subj[:1].upper() + subj[1:]
    verb = m.group("verb")
    rest = m.group("rest").rstrip(".")
    head = subj.split()[-1].lower()
    plural = (head.endswith("s") and not head.endswith(("ss", "us", "is"))) or head in ("users", "we", "they")
    if verb.lower() == "not":
        return f"{subj} {'do' if plural else 'does'} not {rest}."
    verb_form = {"be": "are", "have": "have"}.get(verb.lower(), verb) if plural else _third_person(verb)
    return f"{subj} {verb_form} {rest}.".replace("  ", " ")


def _key_terms(text: str, start: int) -> set[str]:
    window = re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", text[max(0, start - 60) : start])
    return {w.lower().rstrip("s") for w in window[-6:] if w.lower() not in STOPWORDS}


def _quantities(text: str) -> list[tuple[float, str, set[str]]]:
    out = []
    for m in QTY_RE.finditer(text):
        unit = UNIT_CANON.get(m.group("unit").lower())
        if unit:
            out.append((float(m.group("num")), unit, _key_terms(text, m.start())))
    return out


def _is_header_table(header: list[str]) -> dict[str, int] | None:
    cols = {h.strip().lower(): i for i, h in enumerate(header)}

    def find(*names: str) -> int | None:
        for name, i in cols.items():
            if any(n == name or name.startswith(n) for n in names):
                return i
        return None

    idx = {
        "id": find("id", "req id", "requirement id", "ref"),
        "requirement": find("requirement", "description", "statement"),
        "priority": find("priority", "moscow"),
        "criteria": find("acceptance criteria", "acceptance", "criteria"),
        "title": find("title", "name", "summary"),
    }
    if idx["requirement"] is None:
        return None
    return {k: v for k, v in idx.items() if v is not None}


def extract(blocks: list[dict[str, Any]], *, document_kind: str) -> dict[str, Any]:
    reqs: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    counters = {"REQ": 0, "BR": 0, "TR": 0}
    table_headers: dict[int, dict[str, int]] = {}
    current: dict[str, Any] | None = None

    def next_key(prefix: str) -> str:
        while True:
            counters[prefix] += 1
            key = f"{prefix}-{counters[prefix]:03d}"
            if key not in used_keys:
                return key

    def add_requirement(
        key: str | None,
        prefix: str | None,
        statement: str,
        block: dict[str, Any],
        *,
        origin: str,
        confidence: float,
        priority: str | None = None,
        title: str | None = None,
        criteria: list[str] | None = None,
    ) -> dict[str, Any]:
        if key is None or key in used_keys:
            key = next_key("TR" if document_kind == "transcript" else "REQ")
        used_keys.add(key)
        req = {
            "key": key,
            "kind": _kind(prefix, statement),
            "title": _title(statement, title),
            "statement": statement,
            "priority": _priority(statement, priority),
            "origin": origin,
            "block_id": block["block_id"],
            "quote": block["text"],
            "confidence": confidence,
            "acceptance_criteria": [
                {"text": c, "origin": "explicit", "block_id": block["block_id"], "quote": block["text"]}
                for c in (criteria or [])
            ],
        }
        reqs.append(req)
        return req

    for block in blocks:
        text = _norm(block["text"])
        kind = block["kind"]
        section = " > ".join(block.get("section_path", [])).lower()

        if INJECTION_RE.search(text):
            questions.append(
                {
                    "kind": "injection_warning",
                    "question": "This passage contains instructions aimed at automated agents. "
                    "It was not used for "
                    "extraction. Confirm whether it is a genuine requirement.",
                    "block_id": block["block_id"],
                    "quote": block["text"],
                    "blocking": False,
                    "requirement_key": None,
                }
            )
            continue

        if kind == "heading":
            if "acceptance" not in text.lower():
                current = None
            continue

        if kind == "table_header":
            cols = _is_header_table(block.get("cells") or text.split(" | "))
            if cols:
                table_headers[block.get("table", 0)] = cols
            continue

        if kind == "table_row" and block.get("table") in table_headers and block.get("cells"):
            cols = table_headers[block["table"]]
            cells = block["cells"]

            def cell(name: str, _c: dict[str, int] = cols, _cells: list[str] = cells) -> str:
                i = _c.get(name)
                return _cells[i].strip() if i is not None and i < len(_cells) else ""

            statement = cell("requirement")
            if not statement:
                continue
            key, prefix, _ = _strip_id(cell("id")) if cell("id") else (None, None, "")
            criteria = [
                c.strip(" -•")
                for c in re.split(r"[;\n]|(?<=\.)\s+(?=[A-Z])", cell("criteria"))
                if c.strip(" -•")
            ]
            if prefix == "BR":
                rules.append(
                    {
                        "key": key,
                        "statement": statement,
                        "block_id": block["block_id"],
                        "quote": block["text"],
                        "origin": "explicit",
                        "requirement_key": None,
                    }
                )
                continue
            current = add_requirement(
                key,
                prefix,
                statement,
                block,
                origin="explicit",
                confidence=0.95,
                priority=cell("priority") or None,
                title=cell("title") or None,
                criteria=criteria,
            )
            continue

        key, prefix, body = _strip_id(text)
        in_ac_section = "acceptance" in section

        # Acceptance criteria attach to the most recent requirement.
        if (
            current is not None
            and (in_ac_section or GWT_RE.match(body) or AC_PREFIX_RE.match(body))
            and prefix not in ("FR", "NFR", "REQ", "US", "SR", "CON", "BR")
        ):
            current["acceptance_criteria"].append(
                {
                    "text": AC_PREFIX_RE.sub("", body).strip(),
                    "origin": "explicit",
                    "block_id": block["block_id"],
                    "quote": block["text"],
                }
            )
            continue

        if prefix == "BR" or (prefix is None and RULE_RE.search(body) and document_kind != "transcript"):
            rkey = key if key and key not in used_keys else next_key("BR")
            used_keys.add(rkey)
            rules.append(
                {
                    "key": rkey,
                    "statement": body,
                    "block_id": block["block_id"],
                    "quote": block["text"],
                    "origin": "explicit",
                    "requirement_key": current["key"] if current else None,
                }
            )
            continue

        if document_kind == "transcript":
            if body.endswith("?"):
                questions.append(
                    {
                        "kind": "missing_detail",
                        "question": f"Raised in the meeting: {body}",
                        "block_id": block["block_id"],
                        "quote": block["text"],
                        "blocking": False,
                        "requirement_key": None,
                    }
                )
            elif TRANSCRIPT_NEED_RE.search(body) or MODAL_RE.search(body):
                speaker = block.get("speaker")
                statement = body
                current = add_requirement(
                    None,
                    None,
                    statement,
                    block,
                    origin="explicit" if MODAL_RE.search(body) else "inferred",
                    confidence=0.6,
                )
                if speaker:
                    current["speaker"] = speaker
            continue

        if prefix in ("FR", "NFR", "REQ", "US", "SR", "CON") or (
            kind in ("paragraph", "list_item", "table_row") and MODAL_RE.search(body)
        ):
            current = add_requirement(
                key, prefix, body, block, origin="explicit", confidence=0.9 if prefix else 0.75
            )

    # Inferred criteria, ambiguity and missing-detail checks per requirement.
    for req in reqs:
        if not req["acceptance_criteria"]:
            req["acceptance_criteria"].append(
                {
                    "text": inferred_criterion(req["statement"]),
                    "origin": "inferred",
                    "block_id": req["block_id"],
                    "quote": req["quote"],
                }
            )
        vague = sorted({m.group(1).lower() for m in VAGUE_RE.finditer(req["statement"])})
        if vague:
            terms = ", ".join(f"'{t}'" for t in vague)
            questions.append(
                {
                    "kind": "ambiguity",
                    "question": f"{req['key']} uses {terms}, which is not measurable. "
                    "What is the target value?",
                    "block_id": req["block_id"],
                    "quote": req["quote"],
                    "blocking": False,
                    "requirement_key": req["key"],
                }
            )
        if TBD_RE.search(req["statement"]):
            questions.append(
                {
                    "kind": "missing_detail",
                    "question": f"{req['key']} contains an unresolved placeholder (TBD). "
                    "What is the decision?",
                    "block_id": req["block_id"],
                    "quote": req["quote"],
                    "blocking": False,
                    "requirement_key": req["key"],
                }
            )

    # Placeholders anywhere else in the document (open items sections, notes).
    used_blocks = {r["block_id"] for r in reqs} | {r["block_id"] for r in rules}
    for block in blocks:
        if (
            block["block_id"] not in used_blocks
            and block["kind"] != "heading"
            and TBD_RE.search(block["text"])
        ):
            questions.append(
                {
                    "kind": "missing_detail",
                    "question": f'Unresolved item in the source: "{_norm(block["text"])[:160]}"',
                    "block_id": block["block_id"],
                    "quote": block["text"],
                    "blocking": False,
                    "requirement_key": None,
                }
            )

    questions.extend(contradictions(reqs + rules))
    for q in questions:
        q.setdefault("requirement_key", None)
    return {"requirements": reqs, "business_rules": rules, "questions": questions}


def contradictions(
    items: list[dict[str, Any]], existing: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """The same subject constrained to different quantities in the same unit.

    With `existing`, only pairs between new items and existing items are reported (cross-document).
    Items need `key`, `statement`, `block_id` and `quote`.
    """
    pairs: list[tuple[dict[str, Any], dict[str, Any]]]
    if existing is None:
        pairs = [(a, b) for i, a in enumerate(items) for b in items[i + 1 :]]
    else:
        pairs = [(e, n) for n in items for e in existing]
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for a, b in pairs:
        for n1, u1, t1 in _quantities(a["statement"]):
            for n2, u2, t2 in _quantities(b["statement"]):
                shared = t1 & t2
                pair = (a["key"], b["key"])
                if u1 == u2 and n1 != n2 and shared and pair not in seen:
                    seen.add(pair)
                    out.append(
                        {
                            "kind": "contradiction",
                            "question": f"{a['key']} and {b['key']} set different limits "
                            f"for '{sorted(shared)[0]}' "
                            f"({n1:g} vs {n2:g} {u1}). Which is correct?",
                            "block_id": b["block_id"],
                            "quote": b["quote"],
                            "blocking": True,
                            "requirement_key": a["key"],
                        }
                    )
    return out
