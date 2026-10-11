import pytest

from smart_trader.model import timeframe_seconds


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
