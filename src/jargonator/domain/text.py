"""Jargon leakage check: did the jargon reuse the original's content words? (spec.md §3.5)"""

import re

STOP_WORDS: frozenset[str] = frozenset(
    """
    a about above after again against all also am an and any are aren as at be because been
    before being below between both but by can cannot could couldn did didn do does doesn doing
    don down during each even ever every few for from further get gets got had hadn has hasn
    have haven having he her here hers herself him himself his how however into is isn it its
    itself just let like me mine more most much must my myself never no nor not now of off on
    once only or other our ours ourselves out over own really same she should shouldn since so
    some still such than that the their theirs them themselves then there these they this those
    through to too under until up upon very was wasn we were weren what when where which while
    who whom why will with won would wouldn yet you your yours yourself yourselves
    """.split()  # noqa: SIM905 (a word block is far easier to read than a list literal)
)

_WORD = re.compile(r"[a-z]+")


def stem(word: str) -> str:
    """Very light suffix stripping. Enough to match cats/cat, horses/horse, liked/likes."""
    w = word
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("es") and len(w) > 4:
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    elif w.endswith("ing") and len(w) > 5:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def _surface_words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in STOP_WORDS]


def content_words(text: str) -> set[str]:
    """Stemmed, lower-cased words of 3+ letters, excluding stop words."""
    return {stem(w) for w in _surface_words(text)}


def leaked_words(original: str, jargon: str) -> set[str]:
    """Words from the original (as written) whose stem also appears in the jargon."""
    jargon_stems = content_words(jargon)
    return {w for w in _surface_words(original) if stem(w) in jargon_stems}


def leakage_ratio(original: str, jargon: str) -> float:
    """Share of the original's content words that reappear in the jargon."""
    original_stems = content_words(original)
    if not original_stems:
        return 0.0
    return len(original_stems & content_words(jargon)) / len(original_stems)


def is_leaky(original: str, jargon: str, threshold: float = 0.5) -> bool:
    """True if strictly more than ``threshold`` of the content words leaked."""
    return leakage_ratio(original, jargon) > threshold
