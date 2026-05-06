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
