import argparse
import os
from typing import Literal

import log
import dotenv
import uvicorn
from bot_manager import BotManager
from client.binance_client import BinanceSwapClient
from event_loop.binance import BinanceDataEventLoop
from backtest.backtest_runner import BacktestRunner
from backtest.config import BacktestConfig
from model import Symbol

dotenv.load_dotenv()

logger = log.getLogger(__name__)


def create_binance_client(client_type: Literal["MAIN", "COPY"]) -> BinanceSwapClient:
    api_key = os.environ.get(f'BINANCE_API_KEY_{client_type}')
    api_secret = os.environ.get(f'BINANCE_API_SECRET_{client_type}')
    is_test = os.environ.get(f'BINANCE_IS_TEST_{client_type}') == 'True'
    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set')

    logger.info('api_key: %s*****, api_secret: %s*****, is_test: %s',
                api_key[:5], api_secret[:5], is_test)
    binance_client = BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)
    return binance_client


def parse_symbol(raw: str) -> Symbol:
    parts = raw.split('/')
    if len(parts) != 2:
        raise ValueError(f"Invalid symbol format: {raw}. Expected BASE/QUOTE (e.g. DOGE/USDT)")
    return Symbol(base=parts[0], quote=parts[1])


def main() -> None:
    parser = argparse.ArgumentParser(description='Smart Trader')
    parser.add_argument('--mode', choices=['api', 'no-api', 'backtest'], default='api')
    parser.add_argument('--config', default='strategies.yaml')
    parser.add_argument('--symbol', help='Primary trading symbol (e.g. DOGE/USDT)')
    parser.add_argument('--timeframe', help='Primary kline timeframe (e.g. 1m)')
    parser.add_argument('--start', help='Backtest start date (YYYY-MM-DD)')
    parser.add_argument('--end', help='Backtest end date (YYYY-MM-DD)')
    parser.add_argument('--balance', type=float, default=10000.0)
    parser.add_argument('--data-dir', default='data')
    args = parser.parse_args()

    if args.mode == 'backtest':
        if not args.symbol or not args.timeframe or not args.start or not args.end:
            parser.error('--mode backtest requires --symbol, --timeframe, --start, --end')

        config = BacktestConfig(
            config_path=args.config,
            symbol=parse_symbol(args.symbol),
            timeframe=args.timeframe,
            start_date=args.start,
            end_date=args.end,
            initial_balance=args.balance,
            data_dir=args.data_dir,
        )
        runner = BacktestRunner(config)
        print(runner.report())
    elif args.mode == 'no-api':
        BotManager(
            ex_client=create_binance_client("MAIN"),
            el=BinanceDataEventLoop(),
            config_path=args.config,
        ).start_bot()
    else:
        uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == '__main__':
    main()
