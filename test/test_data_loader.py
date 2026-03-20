import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from backtest.data_loader import HistoricalDataLoader
from model import Symbol


class TestEnsureData:
    @pytest.fixture
    def loader(self):
        return HistoricalDataLoader()

    @pytest.fixture
    def symbol(self):
        return Symbol(base="eth", quote="usdt")

    def test_file_naming_without_offset(self, loader, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        expected_path = "data/ETHUSDT_5m_20260301_0s_20260319.csv"

        with patch.object(Path, 'exists', return_value=True):
            result = loader.ensure_data(symbol, "5m", start_time, end_time, "data")

        assert result == expected_path

    def test_file_naming_with_offset(self, loader, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        offset = timedelta(days=30)
        expected_path = "data/ETHUSDT_5m_20260301_2592000s_20260319.csv"

        with patch.object(Path, 'exists', return_value=True):
            result = loader.ensure_data(symbol, "5m", start_time, end_time, "data", offset=offset)

        assert result == expected_path

    def test_download_start_time_with_offset(self, loader, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        offset = timedelta(days=30)
        expected_download_start = datetime(2026, 1, 30, tzinfo=timezone.utc)

        with patch.object(Path, 'exists', return_value=False):
            with patch.object(loader, 'download_and_save_historical_data') as mock_download:
                mock_download.return_value = "mock_path"
                loader.ensure_data(symbol, "5m", start_time, end_time, "data", offset=offset)

        mock_download.assert_called_once()
        call_args = mock_download.call_args
        assert call_args[0][2] == expected_download_start
