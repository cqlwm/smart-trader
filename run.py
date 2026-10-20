import dotenv

from bootstrap import create_binance_exchange
from smart_trader import log
from smart_trader.data_event_loop import BinanceDataEventLoop
from smart_trader.exchange.ports import Exchange
from smart_trader.task.strategy_task import StrategyTask

dotenv.load_dotenv()

log.init_logging()

logger = log.getLogger(__name__)

# main binance exchange
main_exchange: Exchange = create_binance_exchange("main")


def main():
    from smart_trader.config import DB_PATH
    from smart_trader.storage.order_journal import OrderJournal
    from template import zecusdt_trend
    journal = OrderJournal(DB_PATH)
    tasks: list[StrategyTask] = [
        zecusdt_trend.trend_short(main_exchange, journal),
        # zecusdt_trend.grid_long(main_exchange, journal),

        # dogeusdt_trend.trend_short(main_exchange, journal),
        # uniusdt_trend.trend_short(main_exchange, journal),
        # gtcusdt_trend.trend_short(main_exchange, journal),
        # agtusdt_trend.trend_short(main_exchange, journal),
        # icntusdt_trend.trend_short(main_exchange, journal),
        # qusdt_trend.trend_short(main_exchange, journal),
        # wusdt_trend.trend_short(main_exchange, journal),
    ]

    kline_subscribes: list[str] = []
    data_event_loop = BinanceDataEventLoop(kline_subscribes=kline_subscribes)

    for task in tasks:
        for timeframe in task.strategy.timeframes:
            sub_key = task.symbol.binance_ws_sub_kline(timeframe)
            if sub_key not in kline_subscribes:
                kline_subscribes.append(sub_key)
        data_event_loop.add_task(task)

    try:
        # 阻塞运行; 连接断开时由 start() 内部自动重连
        data_event_loop.start()
    except KeyboardInterrupt:
        logger.info("收到退出信号, 停止事件循环")
    finally:
        data_event_loop.stop()


if __name__ == "__main__":
    main()
