import pytest

from jargonator.domain.text import content_words, is_leaky, leakage_ratio, leaked_words, stem


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("cats", "cat"),
        ("cat", "cat"),
        ("horses", "hors"),
        ("horse", "hors"),
        ("likes", "lik"),
        ("liked", "lik"),
        ("liking", "lik"),
        ("boxes", "box"),
        ("babies", "baby"),
        ("walked", "walk"),
        ("glass", "glass"),
        ("glasses", "glass"),
    ],
)
def test_stem(word: str, expected: str) -> None:
    assert stem(word) == expected


def test_content_words_drops_stop_words_short_words_and_case() -> None:
    assert content_words("I have TWO cats, and a dog!") == {"two", "cat", "dog"}


def test_content_words_splits_on_non_letters() -> None:
    assert content_words("I'm running 5k marathons") == {"runn", "marathon"}


def test_content_words_empty() -> None:
    assert content_words("I am so into it") == set()
    assert content_words("") == set()


def test_leaked_words_returns_original_surface_forms() -> None:
    assert leaked_words("I have two cats", "I steward a feline cat portfolio") == {"cats"}


def test_leakage_ratio() -> None:
    # Content words: two, cats → one of the two leaks.
    assert leakage_ratio("I have two cats", "My cat portfolio") == 0.5
    assert leakage_ratio("I have two cats", "Dual feline assets") == 0.0
    assert leakage_ratio("I have two cats", "Two cats indeed") == 1.0


def test_leakage_ratio_with_no_content_words() -> None:
    assert leakage_ratio("I am", "anything at all") == 0.0


def test_is_leaky_threshold_is_strict() -> None:
    assert not is_leaky("I have two cats", "My cat portfolio")  # exactly 0.5
    assert is_leaky("I have two cats", "Two cats indeed")
    assert is_leaky("I have two cats", "My cat portfolio", threshold=0.4)
