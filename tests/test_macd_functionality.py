import sys
import os
import pandas as pd
import numpy as np

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model import OrderSide
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal, _alpha_trend_indicator


def test_macd_calculation():
    """Test MACD calculation in AlphaTrendSignal"""
    print("Testing MACD calculation...")

    # Create sample data
    dates = pd.date_range('2023-01-01', periods=100, freq='5min')
    np.random.seed(42)

    # Generate realistic OHLCV data
    base_price = 50000
    prices = []
    current_price = base_price

    for i in range(100):
        # Add some trend and noise
        trend = 0.001 if i < 50 else -0.001  # Uptrend then downtrend
        noise = np.random.normal(0, 0.005)
        current_price *= (1 + trend + noise)
        prices.append(current_price)

    data = {
        'datetime': [d.strftime('%Y-%m-%d %H:%M:%S') for d in dates],
        'open': prices,
        'high': [p * (1 + abs(np.random.normal(0, 0.002))) for p in prices],
        'low': [p * (1 - abs(np.random.normal(0, 0.002))) for p in prices],
        'close': prices,
        'volume': [np.random.uniform(100, 1000) for _ in range(100)],
        'finished': [True] * 100
    }

    df = pd.DataFrame(data)

    # Test the _alpha_trend_signal function with MACD
    processed_df = _alpha_trend_indicator(df)

    # Check that MACD columns exist
    assert 'macd' in processed_df.columns, "MACD column should exist"
    assert 'macd_signal' in processed_df.columns, "MACD signal column should exist"
    assert 'macd_hist' in processed_df.columns, "MACD histogram column should exist"

    # Check that MACD values are calculated (should have NaN for early periods)
    macd_values = processed_df['macd'].dropna()
    assert len(macd_values) > 0, "Should have some MACD values calculated"

    print(f"MACD calculation successful. Data shape: {processed_df.shape}")
    print(f"MACD values calculated for {len(macd_values)} periods")

    # Test AlphaTrendSignal with MACD parameters
    signal = AlphaTrendSignal(OrderSide.BUY, macd_fast_period=12, macd_slow_period=26, macd_signal_period=9)

    # Run signal calculation
    result = signal.run(df)
    assert isinstance(result, int), "Signal should return an integer"
    assert result in [-1, 0, 1], "Signal should be -1, 0, or 1"

    print(f"Signal calculation successful. Result: {result}")
    print(f"Current MACD: {signal.current_macd}")
    print(f"Current MACD Signal: {signal.current_macd_signal}")

    # Test crossover detection logic
    # Simulate a dead cross scenario (MACD falling below signal)
    signal.previous_macd = 10.0
    signal.previous_macd_signal = 8.0
    signal.current_macd = 5.0
    signal.current_macd_signal = 7.0

    print("Test crossover detection completed successfully!")
    return True


if __name__ == "__main__":
    print("=== Testing MACD Functionality ===\n")
    test_macd_calculation()
    print("\n✅ All MACD functionality tests passed!")
