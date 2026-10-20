"""What a message is about, given the whole conversation (FR-CONV-5, ADR-035).

Follow-ups rarely restate their subject: "make a document on this same", "use this template and give me the
presentation as same", "a detailed report on him". Instead of matching such phrases one by one, the subject the
message names is tested for substance: once request words (make, document, template…), function words and
referring words (this, same, him…) are removed, does anything remain? If not, the message refers back and the
subject is the most recent substantive subject of the conversation. The planner's own reading (LLM) is used when it
passes the same test, so the LLM resolves what it can and this layer guarantees a usable subject either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Words that never carry a subject on their own: function words, request verbs, deliverable nouns, referring words.
_FUNCTION = set("""
a an the of on in at to for from by with about regarding into onto over under as is are was were be been being
and or but nor so than then also too as-well well very just only even please kindly can could would will shall
should may might must do does did done i me my we us our you your it its this that these those there here
same similar such one ones thing things stuff topic subject matter content above below previous prior earlier
former latter last first second next other another again more all both each every any some whole entire
him her his hers them they their theirs he she himself herself itself themselves
""".split())
_REQUEST = set("""
make create generate build write draft prepare produce give get send show provide put together turn convert
use using used apply keep follow want need like love help redo regenerate recreate rebuild update change edit
add include also now same-as based base according basis
presentation presentations deck decks slide slides slideshow ppt pptx powerpoint
document documents doc docs docx word report reports paper papers proposal proposals brief briefs memo
whitepaper article essay summary handout version copy file files format template templates design style layout
spreadsheet spreadsheets workbook workbooks excel xlsx sheet sheets table tables
detail detailed details short long quick concise brief full complete comprehensive in-depth depth new fresh
professional formal simple nice good great
""".split())
_POSSESSIVE = re.compile(r"\b(his|her|its|their)\b", re.I)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9&'/+.\-]*")


def content_words(text: str) -> list[str]:
    """Words of ``text`` that can carry a subject (numbers such as '2026' or '26/11' count)."""
    out = []
    for w in _WORD.findall(text or ""):
        low = w.lower().strip(".'")
        if low.endswith("'s"):
            low = low[:-2]
        if low and low not in _FUNCTION and low not in _REQUEST:
            out.append(w)
    return out


def is_substantive(subject: str) -> bool:
    return bool(content_words(subject))


def _clean(subject: str) -> str:
    s = re.sub(r"\s+", " ", subject or "").strip(" \"'“”.,;:!?")
    return s[:1].upper() + s[1:] if s else s


@dataclass
class ResolvedRequest:
    subject: str  # what the deliverable is about, never a pronoun
    standalone: str  # the request rewritten so it reads without the conversation
    referential: bool = False  # the message referred back to an earlier subject
    source: str = "message"  # message | planner | conversation
    missing: bool = False  # no subject in the message and none earlier in the conversation: ask for it
    notes: list[str] = field(default_factory=list)


def subject_history(context: Any) -> list[str]:
    """Substantive subjects of the conversation, most recent first."""
    seen: list[str] = []
    for s in list(getattr(context, "subject_history", None) or []) + [getattr(context, "current_topic", "") or ""]:
        s = _clean(s)
        if s and is_substantive(s) and s.lower() not in {x.lower() for x in seen}:
            seen.append(s)
    return seen


def message_subject(message: str) -> str:
    """The subject phrase a message names ('a deck on community solar for councils' -> 'community solar for councils')."""
    from app.agents.planner import topic_of

    return _clean(topic_of(message))


def resolve_request(
    message: str,
    context: Any = None,
    *,
    planner_subject: str = "",
    planner_standalone: str = "",
    deliverable: str = "",
) -> ResolvedRequest:
    """Subject and stand-alone wording of ``message`` in its conversation."""
    prior = subject_history(context)
    named = message_subject(message)
    planner_subject = _clean(planner_subject)
    # The planner (LLM) read the whole conversation; accept its subject when it is real and is not just the
    # message echoed back (an echo means it did not resolve anything).
    if planner_subject and is_substantive(planner_subject) and not _echo(planner_subject, message):
        subject, source = planner_subject, "planner"
    elif is_substantive(named):
        subject, source = named, "message"
    elif prior:
        subject, source = prior[0], "conversation"
    else:
        return ResolvedRequest(subject="", standalone=_clean(message), missing=True)

    referential = source == "conversation" or (source == "planner" and not _overlaps(subject, message))
    if source == "message" and prior and _POSSESSIVE.search(named):
        # "his early life" after a deck on Sachin Tendulkar -> "Sachin Tendulkar's early life"
        subject = _clean(_POSSESSIVE.sub(f"{prior[0]}'s", named))
        referential, source = True, "conversation"

    # The planner's rewrite is used when it names the subject; for a follow-up it must not just echo the message.
    standalone = _clean(planner_standalone)
    usable = bool(standalone) and _overlaps(subject, standalone) and not (referential and _echo(standalone, message))
    if not usable:
        standalone = _rewrite(message, named, subject, deliverable) if referential else _clean(message)
    notes = [f"Read as a follow-up on “{subject}”."] if referential else []
    return ResolvedRequest(subject=subject, standalone=standalone, referential=referential, source=source, notes=notes)


def _echo(candidate: str, message: str) -> bool:
    """True when the candidate is (nearly) the raw message, i.e. nothing was resolved."""
    a = [w.lower() for w in _WORD.findall(candidate)]
    b = [w.lower() for w in _WORD.findall(message)]
    if not a or not b:
        return False
    return len(set(a) & set(b)) / max(len(set(a)), 1) > 0.85 and len(a) >= 0.7 * len(b)


def _overlaps(subject: str, text: str) -> bool:
    words = {w.lower() for w in content_words(subject)}
    return bool(words) and bool(words & {w.lower() for w in _WORD.findall(text)})


def _rewrite(message: str, named: str, subject: str, deliverable: str = "") -> str:
    """Put the resolved subject where the message referred to it ('… on this same' -> '… on Gen AI 2026')."""
    text = _clean(message)
    if named and named.lower() in text.lower() and len(named) < 0.6 * len(text):
        i = text.lower().rindex(named.lower())
        return _clean(text[:i] + subject + text[i + len(named):])
    # Nothing in the message names the subject ("use this template and give me the presentation as same"):
    # the request is the deliverable on the earlier subject; template and format choices live in the plan.
    return f"Create a {deliverable or 'deliverable'} on {subject}"


def subjects_from_runs(plans: Iterable[dict[str, Any]]) -> list[str]:
    """Subjects recorded in earlier runs' plans, most recent first (plans newest first)."""
    out: list[str] = []
    for p in plans:
        subj = _clean(p.get("subject") or "")
        if not is_substantive(subj) and p.get("topic") and p.get("action") != "answer":
            subj = message_subject(p.get("topic") or "")
        if subj and is_substantive(subj) and subj.lower() not in {s.lower() for s in out}:
            out.append(subj)
    return out
