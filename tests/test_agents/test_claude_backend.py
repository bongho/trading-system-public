"""ClaudeDirectBackend JSON 파싱 테스트.

_extract_json만 테스트 (anthropic SDK 없이 실행 가능하도록 직접 임포트 우회).
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

# anthropic SDK 없이 _extract_json만 테스트하기 위해 mock 주입
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = MagicMock()

from src.agents.claude_backend import _extract_json


class TestExtractJson:
    def test_json_code_block(self) -> None:
        text = 'Here is my analysis:\n```json\n{"sentiment": "bullish"}\n```\nDone.'
        result = _extract_json(text)
        assert result == {"sentiment": "bullish"}

    def test_raw_json(self) -> None:
        text = '{"approved": true, "risk_score": 0.3}'
        result = _extract_json(text)
        assert result["approved"] is True

    def test_json_with_backticks_no_label(self) -> None:
        text = '```\n{"strategy_id": "rsi"}\n```'
        result = _extract_json(text)
        assert result["strategy_id"] == "rsi"

    def test_json_embedded_in_text(self) -> None:
        text = 'Result: {"param_changes": {"rsi": 21}} end'
        result = _extract_json(text)
        assert result["param_changes"]["rsi"] == 21

    def test_nested_json(self) -> None:
        text = '```json\n{"indicators": {"rsi": 35.2, "bb": 0.1}}\n```'
        result = _extract_json(text)
        assert result["indicators"]["rsi"] == 35.2
