# BacktestRunner 统一入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify backtest and live trading entry points into `run.py` via a `BacktestRunner` class that wraps `BotManager`.

**Architecture:** `BacktestRunner` encapsulates backtest-specific setup (data loading, client-eventloop wiring, result analysis) and delegates to `BotManager` for strategy loading and event loop execution. `BotManager` is zero-modified. `run_backtest.py` is deleted.

**Tech Stack:** Python 3.11, dataclasses, pytest, BacktestClient, BacktestEventLoop, BotManager, TradeAnalysis

---

### Task 1: Update BacktestConfig to support YAML-driven strategy loading

The existing `BacktestConfig` has `strategy_type` and `strategy_config` fields for direct strategy instantiation. We need to replace these with `config_path` so that `BacktestRunner` delegates strategy loading to `BotManager → StrategyLoader`.

**Files:**
- Modify: `backtest/config.py`
- Modify: `test/test_backtest_rewrite.py:44-76` (update `TestBacktestConfig`)

- [ ] **Step 1: Write the failing test for updated BacktestConfig**

Add new test cases to `TestBacktestConfig` in `test/test_backtest_rewrite.py`:

```python
class TestBacktestConfig:
    def test_frozen_dataclass(self) -> None:
        config = BacktestConfig(
            config_path="strategies.yaml",
            symbol=SYMBOL,
            timeframe="1m",
            start_date="2025-01-01",
            end_date="2025-06-01",
        )
        assert config.config_path == "strategies.yaml"
        assert config.initial_balance == 10000.0
        assert config.maker_fee == 0.0002
        assert config.taker_fee == 0.0004
        assert config.start_index == 300

        with pytest.raises(AttributeError):
            config.initial_balance = 999  # type: ignore[misc]

    def test_custom_fees_and_start_index(self) -> None:
        config = BacktestConfig(
            config_path="custom.yaml",
            symbol=SYMBOL,
            timeframe="5m",
            start_date="2025-01-01",
            end_date="2025-02-01",
            initial_balance=50000.0,
            maker_fee=0.001,
            taker_fee=0.002,
            start_index=0,
            data_dir="my_data",
        )
        assert config.initial_balance == 50000.0
        assert config.maker_fee == 0.001
        assert config.taker_fee == 0.002
        assert config.start_index == 0
        assert config.data_dir == "my_data"
        assert config.config_path == "custom.yaml"

    def test_default_config_path(self) -> None:
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe="1m",
            start_date="2025-01-01",
            end_date="2025-06-01",
        )
        assert config.config_path == "strategies.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_backtest_rewrite.py::TestBacktestConfig -v`
Expected: FAIL — `BacktestConfig.__init__()` missing `strategy_type` / `strategy_config` etc.

- [ ] **Step 3: Update BacktestConfig implementation**

Replace `backtest/config.py` with:

```python
from dataclasses import dataclass

from model import Symbol


@dataclass(frozen=True)
class BacktestConfig:
    config_path: str = "strategies.yaml"
    symbol: Symbol
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float = 10000.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0004
    extra_timeframes: tuple[str, ...] = ()
    data_dir: str = "data"
    start_index: int = 300
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_backtest_rewrite.py::TestBacktestConfig -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/config.py test/test_backtest_rewrite.py
git commit -m "refactor: update BacktestConfig to use config_path instead of strategy_type/config"
```

---

### Task 2: Implement BacktestRunner

Create the `BacktestRunner` class that wires together `BacktestClient`, `BacktestEventLoop`, and `BotManager`.

**Files:**
- Create: `backtest/backtest_runner.py`
- Create: `test/test_backtest_runner.py`

- [ ] **Step 1: Write failing tests for BacktestRunner**

Create `test/test_backtest_runner.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from model import Symbol, Kline
from backtest.config import BacktestConfig
from backtest.backtest_runner import BacktestRunner
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
from persistence.order_repository import InMemoryOrderRepository


SYMBOL = Symbol(base='ETH', quote='USDT')
TS_BASE = 1_700_000_000_000


def _make_klines(count: int) -> list[Kline]:
    return [
        Kline(
            symbol=SYMBOL,
            timeframe='1m',
            open=2000.0 + i,
            high=2010.0 + i,
            low=1990.0 + i,
            close=2005.0 + i,
            volume=100.0,
            timestamp=TS_BASE + i * 60_000,
            finished=True,
        )
        for i in range(count)
    ]


class TestBacktestRunnerInit:
    def test_creates_backtest_client(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        assert isinstance(runner._backtest_client, BacktestClient)

    def test_creates_backtest_event_loop(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
            start_index=0,
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        assert isinstance(runner._event_loop, BacktestEventLoop)
        assert runner._event_loop.backtest_client is runner._backtest_client

    def test_creates_bot_manager(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            config_path="test_strategies.yaml",
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        assert runner._bot_manager is not None
        assert runner._bot_manager._config_path == "test_strategies.yaml"


class TestBacktestRunnerRun:
    def test_run_returns_analysis_dict(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
            start_index=0,
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls, \
             patch.object(BacktestRunner, '_start_bot') as mock_start:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)
            result = runner.run()

        assert 'summary' in result
        assert 'risk_metrics' in result
        assert 'trade_metrics' in result

    def test_run_calls_bot_start(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
            start_index=0,
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        with patch.object(runner._bot_manager, 'start_bot') as mock_start:
            runner.run()
            mock_start.assert_called_once()


class TestBacktestRunnerReport:
    def test_report_calls_bot_start_and_trade_analysis(self) -> None:
        klines = _make_klines(10)
        config = BacktestConfig(
            symbol=SYMBOL,
            timeframe='1m',
            start_date='2025-01-01',
            end_date='2025-02-01',
            start_index=0,
        )

        with patch('backtest.backtest_runner.KlineDataStore') as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_store.ensure_data.return_value = "data/mock.csv"
            mock_store.load_csv.return_value = klines

            runner = BacktestRunner(config)

        with patch.object(runner._bot_manager, 'start_bot'), \
             patch('backtest.backtest_runner.TradeAnalysis') as mock_ta_cls:
            mock_ta = MagicMock()
            mock_ta.report.return_value = "BACKTEST REPORT\n..."
            mock_ta_cls.return_value = mock_ta

            report = runner.report()

        mock_ta_cls.assert_called_once()
        assert "BACKTEST REPORT" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_backtest_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.backtest_runner'`

- [ ] **Step 3: Implement BacktestRunner**

Create `backtest/backtest_runner.py`:

```python
import logging
from typing import Any

from bot_manager import BotManager
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.config import BacktestConfig
from backtest.kline_data_store import KlineDataStore
from backtest.trade_analysis import TradeAnalysis
from persistence.order_repository import InMemoryOrderRepository

logger = logging.getLogger(__name__)


class BacktestRunner:
    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._backtest_client = self._create_backtest_client()
        all_klines = self._backtest_client.get_all_klines()

        if not all_klines:
            raise ValueError("No historical data loaded for backtest")

        self._event_loop = BacktestEventLoop(
            historical_klines=all_klines,
            on_progress_callback=self._progress_callback,
            start_index=config.start_index,
        )
        self._event_loop.set_backtest_client(self._backtest_client)

        self._bot_manager = BotManager(
            ex_client=self._backtest_client,
            el=self._event_loop,
            config_path=config.config_path,
        )

        logger.info(
            "BacktestRunner initialized: %s %s %s~%s",
            config.symbol.binance(), config.timeframe,
            config.start_date, config.end_date,
        )

    def _create_backtest_client(self) -> BacktestClient:
        data_store = KlineDataStore()
        return BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=self._config.initial_balance,
            maker_fee=self._config.maker_fee,
            taker_fee=self._config.taker_fee,
            data_store=data_store,
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            start_date=self._config.start_date,
            end_date=self._config.end_date,
            extra_timeframes=self._config.extra_timeframes,
            data_dir=self._config.data_dir,
        )

    def _progress_callback(self, current: int, total: int) -> None:
        if current % 1000 == 0:
            progress = (current / total) * 100
            logger.info("Backtest progress: %.1f%% (%d/%d)", progress, current, total)

    def run(self) -> dict[str, Any]:
        self._bot_manager.start_bot()
        self._event_loop.stop()

        trade_analysis = TradeAnalysis(
            self._backtest_client,
            initial_balance=self._config.initial_balance,
        )
        analysis = trade_analysis.analyze()

        logger.info(
            "Backtest completed: %d trades, final balance: $%.2f",
            analysis['summary']['total_trades'],
            self._backtest_client.get_final_balance(),
        )

        return analysis

    def report(self) -> str:
        self._bot_manager.start_bot()
        self._event_loop.stop()

        trade_analysis = TradeAnalysis(
            self._backtest_client,
            initial_balance=self._config.initial_balance,
        )
        return trade_analysis.report()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_backtest_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/backtest_runner.py test/test_backtest_runner.py
git commit -m "feat: add BacktestRunner wrapping BotManager for backtest execution"
```

---

### Task 3: Update run.py to support --mode backtest

Add argparse-based mode selection to `run.py` so it can serve as the unified entry point.

**Files:**
- Modify: `run.py`
- Create: `test/test_run.py`

- [ ] **Step 1: Write failing test for run.py backtest mode**

Create `test/test_run.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from model import Symbol
from backtest.config import BacktestConfig


class TestParseSymbol:
    def test_valid_symbol(self) -> None:
        from run import parse_symbol
        symbol = parse_symbol("DOGE/USDT")
        assert symbol == Symbol(base='DOGE', quote='USDT')

    def test_invalid_symbol_raises(self) -> None:
        from run import parse_symbol
        with pytest.raises(ValueError, match="Invalid symbol format"):
            parse_symbol("INVALID")


class TestRunBacktestMode:
    def test_backtest_mode_creates_runner(self) -> None:
        with patch('run.BacktestRunner') as mock_runner_cls, \
             patch('sys.argv', ['run.py', '--mode', 'backtest',
                                '--symbol', 'DOGE/USDT',
                                '--timeframe', '1m',
                                '--start', '2025-01-01',
                                '--end', '2025-06-01']):
            mock_runner = MagicMock()
            mock_runner.report.return_value = "BACKTEST REPORT\n..."
            mock_runner_cls.return_value = mock_runner

            from run import main
            main()

            mock_runner_cls.assert_called_once()
            config_arg = mock_runner_cls.call_args[0][0]
            assert isinstance(config_arg, BacktestConfig)
            assert config_arg.symbol == Symbol(base='DOGE', quote='USDT')
            assert config_arg.timeframe == '1m'
            assert config_arg.start_date == '2025-01-01'
            assert config_arg.end_date == '2025-06-01'
            mock_runner.report.assert_called_once()

    def test_no_api_mode_creates_bot_manager(self) -> None:
        with patch('run.BotManager') as mock_bm_cls, \
             patch('run.create_binance_client') as mock_client, \
             patch('run.BinanceDataEventLoop') as mock_el_cls, \
             patch('sys.argv', ['run.py', '--mode', 'no-api']):
            mock_client.return_value = MagicMock()
            mock_el_cls.return_value = MagicMock()

            from run import main
            main()

            mock_bm_cls.assert_called_once()

    def test_api_mode_starts_uvicorn(self) -> None:
        with patch('run.uvicorn') as mock_uvicorn, \
             patch('sys.argv', ['run.py', '--mode', 'api']):
            from run import main
            main()

            mock_uvicorn.run.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_run.py -v`
Expected: FAIL — `run.py` doesn't have `--mode backtest` support

- [ ] **Step 3: Rewrite run.py with argparse and backtest mode**

Replace `run.py` with:

```python
import argparse
import os
from typing import Literal

import log
import dotenv
import uvicorn
import sys
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_run.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run.py test/test_run.py
git commit -m "feat: add --mode backtest to run.py unified entry point"
```

---

### Task 4: Delete run_backtest.py and update imports

Remove the old backtest entry file. Verify no other files import from it.

**Files:**
- Delete: `run_backtest.py`
- Verify: no imports reference `run_backtest`

- [ ] **Step 1: Verify no imports reference run_backtest**

Run: `grep -r "run_backtest" /Users/li/projects/qt/smart-trader --include="*.py" -l | grep -v __pycache__ | grep -v .venv`
Expected: Only `run_backtest.py` itself (and possibly this plan file)

- [ ] **Step 2: Delete run_backtest.py**

```bash
git rm run_backtest.py
```

- [ ] **Step 3: Run full test suite to verify nothing is broken**

Run: `uv run pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: delete run_backtest.py, unified entry via run.py --mode backtest"
```

---

### Task 5: Run full test suite and type check

Final verification that everything works together.

**Files:**
- No new files

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest test/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run mypy type check**

Run: `uv run mypy backtest/backtest_runner.py run.py --ignore-missing-imports`
Expected: No errors

- [ ] **Step 3: Verify backtest mode CLI works with --help**

Run: `uv run python run.py --help`
Expected: Shows `--mode {api,no-api,backtest}` and backtest-related options

- [ ] **Step 4: Verify backtest mode validates required args**

Run: `uv run python run.py --mode backtest 2>&1 || true`
Expected: Error message about missing `--symbol`, `--timeframe`, `--start`, `--end`
