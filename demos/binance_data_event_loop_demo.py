"""BinanceDataEventLoop 使用示例 / 验证脚本

运行:
    uv run python demos/binance_data_event_loop_demo.py
    uv run python demos/binance_data_event_loop_demo.py --base DOGE --timeframe 1m --seconds 15
    uv run python demos/binance_data_event_loop_demo.py --streams btcusdt@trade

说明:
    1. 仅订阅公开推送, 无需 API Key。
    2. 运行 --seconds 秒后自动关闭连接, 用于快速验证事件循环是否正常。
    3. 默认订阅该交易对的K线流, 可用 --streams 覆盖(逗号分隔)。

注意:
    Binance 于 2026-04-23 停用旧的 wss://fstream.binance.com/ws 与 /stream,
    并将流量拆分为 /public、/market、/private。K线流属于 /market, 因此必须使用
    wss://fstream.binance.com/market/stream, 旧地址下K线不再推送。
"""

import argparse
import json
import threading
import time

import websocket

from smart_trader import log
from smart_trader.data_event_loop import BinanceDataEventLoop, Task
from smart_trader.model import Symbol

log.init_logging()

logger = log.getLogger(__name__)


class CountStreamTask(Task):
    def __init__(self) -> None:
        super().__init__()
        self.name = "CountStreamTask"
        self.counts: dict[str, int] = {}

    def run(self, data: str) -> None:
        try:
            stream = json.loads(data).get("stream", "unknown")
        except json.JSONDecodeError:
            stream = "unknown"
        self.counts[stream] = self.counts.get(stream, 0) + 1
        count = self.counts[stream]
        if count <= 3:
            logger.info("[Task] %s 第 %s 条: %s", stream, count, data)


class DemoDataEventLoop(BinanceDataEventLoop):
    """保留 ws 引用, 便于按时间主动关闭"""

    def __init__(self, kline_subscribes: list[str]) -> None:
        super().__init__(kline_subscribes)
        self.ws: websocket.WebSocketApp | None = None

    def on_open(self, ws: websocket.WebSocketApp) -> None:
        self.ws = ws
        super().on_open(ws)

    def close_after(self, seconds: float) -> None:
        def _worker() -> None:
            time.sleep(seconds)
            logger.info("### 到达 %ss, 主动关闭连接 ###", seconds)
            if self.ws is not None:
                self.ws.close()  # type: ignore[reportUnknownMemberType]

        threading.Thread(target=_worker, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="BinanceDataEventLoop demo")
    parser.add_argument("--base", default="BTC", help="交易对基础币, 默认 BTC")
    parser.add_argument("--quote", default="USDT", help="交易对计价币, 默认 USDT")
    parser.add_argument("--timeframe", default="1m", help="K线周期, 默认 1m")
    parser.add_argument("--seconds", type=float, default=15.0, help="运行时长(秒)")
    parser.add_argument(
        "--streams", default="", help="自定义订阅流(逗号分隔), 默认使用该交易对K线流"
    )
    args = parser.parse_args()

    symbol = Symbol(base=args.base.upper(), quote=args.quote.upper())
    if args.streams:
        streams = [s.strip() for s in args.streams.split(",") if s.strip()]
    else:
        streams = [symbol.binance_ws_sub_kline(args.timeframe)]

    task = CountStreamTask()
    loop = DemoDataEventLoop(kline_subscribes=streams)
    loop.add_task(task)
    loop.close_after(args.seconds)

    logger.info("### 开始订阅 %s, %ss 后自动退出 ###", streams, args.seconds)
    loop.start()  # 阻塞直到连接关闭
    logger.info("### 退出 ### 各流消息数: %s", task.counts)


if __name__ == "__main__":
    main()
