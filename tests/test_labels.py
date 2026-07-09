"""
Unit tests for the label fuzzy-matching logic.
No Gmail API calls are made — only the pure Python matching function is tested.
"""

from __future__ import annotations

from app.gmail.labels import _normalise, find_best_label_match


class TestNormalise:
    def test_strips_spaces(self):
        assert _normalise("Work AI") == "workai"

    def test_strips_hyphens(self):
        assert _normalise("work-ai") == "workai"

    def test_strips_underscores(self):
        assert _normalise("work_ai") == "workai"

    def test_lowercase(self):
        assert _normalise("FINANCE") == "finance"

    def test_mixed(self):
        assert _normalise("Taylor & Francis") == "taylor&francis"


class TestFuzzyMatch:
    _label_map = {
        "Work": "Label_1",
        "Finance": "Label_2",
        "GitHub": "Label_3",
        "Newsletter": "Label_4",
        "Machine Learning": "Label_5",
    }

    def test_exact_match(self):
        name, lid, score = find_best_label_match("Work", self._label_map)
        assert name == "Work"
        assert lid == "Label_1"
        assert score == 100

    def test_case_insensitive_match(self):
        name, lid, score = find_best_label_match("finance", self._label_map)
        assert name == "Finance"

    def test_no_match_below_threshold(self):
        name, lid, score = find_best_label_match(
            "CompletelyUnrelated", self._label_map, threshold=85
        )
        assert name is None
        assert lid is None

    def test_fuzzy_match_with_typo(self):
        # "Finanse" should still match "Finance" at high score
        name, lid, score = find_best_label_match(
            "Finanse", self._label_map, threshold=70
        )
        assert name == "Finance"

    def test_empty_label_map(self):
        name, lid, score = find_best_label_match("Work", {})
        assert name is None
        assert lid is None
        assert score == 0

    def test_multi_word_label(self):
        name, lid, score = find_best_label_match("machine learning", self._label_map)
        assert name == "Machine Learning"
