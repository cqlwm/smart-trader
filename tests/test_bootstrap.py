"""bootstrap 凭据装配测试(构造器打桩, 不触网)"""

from typing import Any

import pytest

import bootstrap
from bootstrap import create_binance_client


def patch_client_with(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """打桩 BinanceSwapClient, 返回构造参数记录列表"""
    created: list[dict[str, Any]] = []

    class FakeBinanceClient:
        def __init__(
            self, api_key: str, api_secret: str, is_test: bool = False
        ) -> None:
            created.append(
                {"api_key": api_key, "api_secret": api_secret, "is_test": is_test}
            )

    monkeypatch.setattr(bootstrap, "BinanceSwapClient", FakeBinanceClient)
    return created


def test_reads_credentials_from_typed_env(monkeypatch: pytest.MonkeyPatch):
    created = patch_client_with(monkeypatch)
    monkeypatch.setenv("BINANCE_API_KEY_MAIN", "key123456")
    monkeypatch.setenv("BINANCE_API_SECRET_MAIN", "secret654321")
    monkeypatch.setenv("BINANCE_IS_TEST_MAIN", "True")

    create_binance_client("main")

    assert created == [
        {"api_key": "key123456", "api_secret": "secret654321", "is_test": True}
    ], "凭据必须按类型后缀从环境变量读取"


@pytest.mark.parametrize("is_test_env", ["true", "True ", "1", ""])
def test_is_test_requires_exact_true(monkeypatch: pytest.MonkeyPatch, is_test_env: str):
    """is_test 只认字符串 'True', 其余一律视为生产环境"""
    created = patch_client_with(monkeypatch)
    monkeypatch.setenv("BINANCE_API_KEY_MAIN", "k")
    monkeypatch.setenv("BINANCE_API_SECRET_MAIN", "s")
    monkeypatch.setenv("BINANCE_IS_TEST_MAIN", is_test_env)

    create_binance_client("main")

    assert created[-1]["is_test"] is False, f"'{is_test_env}' 不应开启测试模式"


def test_missing_credentials_raise(monkeypatch: pytest.MonkeyPatch):
    patch_client_with(monkeypatch)
    monkeypatch.delenv("BINANCE_API_KEY_MAIN", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET_MAIN", raising=False)

    with pytest.raises(ValueError, match="BINANCE_API_KEY"):
        create_binance_client("main")
