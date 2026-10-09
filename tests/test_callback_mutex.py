"""回调互斥测试

覆盖:
- _on_kline_finished 阻塞等待, 永不被跳过（交易决策必须执行）
- _on_kline 非阻塞, finished 正在执行时跳过（盘中更新是临时的）
"""

import threading

from smart_trader.model import Kline, Symbol
from smart_trader.strategy import SingleTimeframeStrategy
from tests.fakes import FakeMarketDataPort

SYMBOL = Symbol(base="DOGE", quote="USDT")


class BlockingStrategy(SingleTimeframeStrategy):
    """_on_kline_finished 会阻塞直到 test 释放 event"""

    def __init__(self, market: FakeMarketDataPort) -> None:
        super().__init__("1m", market)
        self.callbacks: list[str] = []
        self._hold = threading.Event()
        self._started = threading.Event()

    def _on_kline(self) -> None:
        self.callbacks.append("on_kline")

    def _on_kline_finished(self) -> None:
        self._started.set()
        self.callbacks.append("on_kline_finished:start")
        self._hold.wait(timeout=2.0)
        self.callbacks.append("on_kline_finished:done")

    def release(self) -> None:
        self._hold.set()


def build_kline(finished: bool = True) -> Kline:
    return Kline(
        symbol=SYMBOL,
        timeframe="1m",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
        timestamp=1_700_000_000_000,
        finished=finished,
    )


def test_finished_kline_never_skipped():
    """_on_kline_finished 阻塞等待锁, 不被跳过"""
    market = FakeMarketDataPort()
    market.set_klines([build_kline()])
    strategy = BlockingStrategy(market)

    # 第一根 finished kline: 先跑起来, 阻塞在 _on_kline_finished
    t1 = threading.Thread(target=strategy.run, args=(build_kline(),))
    t1.start()
    assert strategy._started.wait(timeout=1.0), "_on_kline_finished 应在 1 秒内启动"

    # 第二根 finished kline: 必须排队等待, 不能跳过
    done2 = threading.Event()

    def run_second() -> None:
        strategy.run(build_kline())
        done2.set()

    t2 = threading.Thread(target=run_second)
    t2.start()

    # 给 t2 足够时间到达 _call_on_kline_finished
    assert not done2.wait(timeout=0.2), (
        "第二根 finished kline 不应在第一根完成前执行"
    )

    # 放行第一根
    strategy.release()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert not t2.is_alive(), "第二根 finished kline 必须被执行, 不能被跳过"
    assert strategy.callbacks.count("on_kline_finished:done") == 2, (
        "两根 finished kline 都必须完成 _on_kline_finished"
    )


def test_on_kline_skipped_when_finished_running():
    """_on_kline 在 _on_kline_finished 执行期间被跳过"""
    market = FakeMarketDataPort()
    market.set_klines([build_kline()])
    strategy = BlockingStrategy(market)

    # 先跑一根 finished kline, 阻塞在 _on_kline_finished
    t = threading.Thread(target=strategy.run, args=(build_kline(),))
    t.start()
    assert strategy._started.wait(timeout=1.0)

    # 清空已记录的回调, 只关注后续行为
    strategy.callbacks.clear()

    # 推一根未完成 kline: on_kline 应被跳过
    strategy.run(build_kline(finished=False))

    assert "on_kline" not in strategy.callbacks, (
        "finished 正在执行时, on_kline 应被跳过"
    )

    # 放行
    strategy.release()
    t.join(timeout=2.0)
