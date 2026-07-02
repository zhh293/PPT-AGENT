from __future__ import annotations

import pytest

from ppt_agent.assembly.layout_fit import assert_text_preserved


def test_text_preservation_comparison() -> None:
    assert_text_preserved(["A"], ["A", "B"])
    with pytest.raises(AssertionError):
        assert_text_preserved(["A"], ["B"])
