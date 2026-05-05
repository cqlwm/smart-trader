import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from backtest.kline_data_store import KlineDataStore
from model import Symbol


class TestEnsureData:
    @pytest.fixture
    def store(self):
        return KlineDataStore()

    @pytest.fixture
    def symbol(self):
        return Symbol(base="eth", quote="usdt")

    def test_file_naming_without_offset(self, store, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        expected_path = "data/ETHUSDT_5m_20260301_0s_20260319.csv"

        with patch.object(Path, 'exists', return_value=True):
            result = store.ensure_data(symbol, "5m", start_time, end_time, "data")

        assert result == expected_path

    def test_file_naming_with_offset(self, store, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        offset = timedelta(days=30)
        expected_path = "data/ETHUSDT_5m_20260301_2592000s_20260319.csv"

        with patch.object(Path, 'exists', return_value=True):
            result = store.ensure_data(symbol, "5m", start_time, end_time, "data", offset=offset)

        assert result == expected_path

    def test_download_start_time_with_offset(self, store, symbol):
        start_time = "2026-03-01"
        end_time = "2026-03-19"
        offset = timedelta(days=30)
        expected_download_start = datetime(2026, 1, 30, tzinfo=timezone.utc)

        with patch.object(Path, 'exists', return_value=False):
            with patch.object(store, 'download_and_save_historical_data') as mock_download:
                mock_download.return_value = "mock_path"
                store.ensure_data(symbol, "5m", start_time, end_time, "data", offset=offset)

        mock_download.assert_called_once()
        call_args = mock_download.call_args
        assert call_args[0][2] == expected_download_start


class TestRemovedMethods:
    @pytest.fixture
    def store(self):
        return KlineDataStore()

    def test_no_load_json(self, store):
        assert not hasattr(store, 'load_json')

    def test_no_load_from_dataframe(self, store):
        assert not hasattr(store, 'load_from_dataframe')

    def test_no_filter_by_date_range(self, store):
        assert not hasattr(store, 'filter_by_date_range')

    def test_no_get_price_series(self, store):
        assert not hasattr(store, 'get_price_series')
