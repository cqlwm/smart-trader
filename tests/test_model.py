import pytest
from pydantic import ValidationError

from smart_trader.model import Position, PositionSide, Symbol, timeframe_seconds


class TestTimeframeSeconds:
    @pytest.mark.parametrize(
        ("timeframe", "expected"),
        [
            ("1s", 1),
            ("1m", 60),
            ("5m", 300),
            ("15m", 900),
            ("1h", 3600),
            ("4h", 14_400),
            ("1d", 86_400),
            ("1w", 604_800),
        ],
    )
    def test_parses_each_unit(self, timeframe: str, expected: int):
        assert timeframe_seconds(timeframe) == expected, (
            f"{timeframe} 应换算为 {expected} 秒"
        )

    @pytest.mark.parametrize(
        "timeframe", ["", "m", "1", "1x", "1M", "1.5h", "-1m", "mm"]
    )
    def test_rejects_invalid_timeframe(self, timeframe: str):
        """非法周期必须抛 ValueError, 不允许静默返回错误秒数"""
        with pytest.raises(ValueError):
            timeframe_seconds(timeframe)


class TestPosition:
    def test_entry_price_is_required(self):
        """entry_price 必填: 0.0 是'看起来合法实则错误'的成本价, 不允许默认值伪装"""
        with pytest.raises(ValidationError, match="entry_price"):
            Position(
                symbol=Symbol(base="DOGE", quote="USDT"),
                side=PositionSide.LONG,
                quantity=100.0,
            )  # type: ignore[call-arg]
