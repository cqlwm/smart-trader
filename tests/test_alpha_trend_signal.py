import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os
import ccxt

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model import OrderSide
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal


def fetch_klines_from_ccxt(symbol: str = 'BTC/USDT', timeframe: str = '1m', limit: int = 1000) -> pd.DataFrame:
    """使用ccxt获取真实K线数据"""
    try:
        # 使用Binance交易所获取数据（无需API密钥）
        exchange = ccxt.binance(
            {
                'options': {
                    'defaultType': 'future',
                }
            }
        )
        
        # 获取OHLCV数据
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
        # 转换为DataFrame
        data = []
        for candle in ohlcv:
            timestamp, open_price, high_price, low_price, close_price, volume = candle
            dt = datetime.fromtimestamp(timestamp / 1000)
            
            data.append({
                'datetime': dt.strftime('%Y-%m-%d %H:%M:%S'),
                'open': open_price,
                'high': high_price,
                'low': low_price,
                'close': close_price,
                'volume': volume,
                'finished': True
            })
        
        return pd.DataFrame(data)
    
    except Exception as e:
        print(f"获取K线数据失败: {e}")
        print("将使用模拟数据进行测试")
        raise e

def test_alpha_trend_signal_basic():
    """测试AlphaTrendSignal基本功能，并保存信号数据到CSV"""
    print("获取真实K线数据进行基础测试...")
    klines = fetch_klines_from_ccxt('BTC/USDT', '5m', 500)  # 使用更多数据进行分析
    
    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    sell_signal = AlphaTrendSignal(OrderSide.SELL)
    
    # 收集所有信号数据
    all_signals = []
    buy_signals = []
    sell_signals = []
    
    print(f"开始分析{len(klines)}根K线，收集信号数据...")
    
    # 逐行处理，收集信号和alpha_trend值
    for i in range(50, len(klines)):  # 从第50行开始，确保有足够的历史数据
        current_data = klines.iloc[:i+1].copy()
        
        # 获取买入和卖出信号
        buy_result = buy_signal.run(current_data)
        sell_result = sell_signal.run(current_data)
        
        # 计算alpha_trend值（需要重新计算来获取当前值）
        from strategy.alpha_trend_signal.alpha_trend_signal import _alpha_trend_indicator
        processed_data = _alpha_trend_indicator(current_data.copy())
        
        # 获取最新的数据
        latest_row = processed_data.iloc[-1]
        
        # 记录所有数据点
        signal_data = {
            'datetime': latest_row['datetime'],
            'price': latest_row['close'],
            'alpha_trend': latest_row['alpha_trend'] if pd.notna(latest_row['alpha_trend']) else None,
            'buy_signal': buy_result,
            'sell_signal': sell_result,
            'atr': latest_row.get('atr', None),
            'mfi': latest_row.get('mfi', None),
            'index': i
        }
        all_signals.append(signal_data)
        
        # 记录买入信号
        if buy_result == 1:
            buy_signals.append({
                'datetime': latest_row['datetime'],
                'price': latest_row['close'],
                'alpha_trend': latest_row['alpha_trend'] if pd.notna(latest_row['alpha_trend']) else None,
                'signal_type': 'BUY',
                'atr': latest_row.get('atr', None),
                'mfi': latest_row.get('mfi', None),
                'index': i
            })
            
        # 记录卖出信号
        if sell_result == -1:
            sell_signals.append({
                'datetime': latest_row['datetime'], 
                'price': latest_row['close'],
                'alpha_trend': latest_row['alpha_trend'] if pd.notna(latest_row['alpha_trend']) else None,
                'signal_type': 'SELL',
                'atr': latest_row.get('atr', None),
                'mfi': latest_row.get('mfi', None),
                'index': i
            })
    
    # 保存所有信号数据
    all_signals_df = pd.DataFrame(all_signals)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 保存完整数据
    all_signals_file = f'data/alpha_trend_all_signals_{timestamp}.csv'
    all_signals_df.to_csv(all_signals_file, index=False)
    print(f"已保存完整信号数据到: {all_signals_file}")
    
    # 保存买入信号
    if buy_signals:
        buy_signals_df = pd.DataFrame(buy_signals)
        buy_signals_file = f'data/alpha_trend_buy_signals_{timestamp}.csv'
        buy_signals_df.to_csv(buy_signals_file, index=False)
        print(f"已保存{len(buy_signals)}个买入信号到: {buy_signals_file}")
    
    # 保存卖出信号
    if sell_signals:
        sell_signals_df = pd.DataFrame(sell_signals)
        sell_signals_file = f'data/alpha_trend_sell_signals_{timestamp}.csv'
        sell_signals_df.to_csv(sell_signals_file, index=False)
        print(f"已保存{len(sell_signals)}个卖出信号到: {sell_signals_file}")
    
    # 合并买卖信号
    if buy_signals or sell_signals:
        combined_signals = buy_signals + sell_signals
        combined_signals_df = pd.DataFrame(combined_signals)
        combined_signals_df = combined_signals_df.sort_values('datetime')
        
        combined_signals_file = f'data/alpha_trend_combined_signals_{timestamp}.csv'
        combined_signals_df.to_csv(combined_signals_file, index=False)
        print(f"已保存合并信号数据到: {combined_signals_file}")
        
        # 显示信号统计
        print("\n信号统计:")
        print(f"买入信号次数: {len(buy_signals)}")
        print(f"卖出信号次数: {len(sell_signals)}")
        print(f"信号频率: {(len(buy_signals) + len(sell_signals)) / len(all_signals) * 100:.2f}%")
        
        # 显示最近的几个信号
        print("\n最近5个交易信号:")
        recent_signals = combined_signals_df.tail(5)
        for _, signal in recent_signals.iterrows():
            alpha_trend_str = f"{signal['alpha_trend']:.4f}" if pd.notna(signal['alpha_trend']) else 'N/A'
            print(f"  {signal['datetime']} - {signal['signal_type']} - "
                  f"价格: {signal['price']:.2f} - Alpha Trend: {alpha_trend_str}")
    
    # 基础验证
    result = buy_signal.run(klines)
    assert isinstance(result, int)
    assert result in [-1, 0, 1]
    print(f"\n基础测试通过，最新买入信号: {result}")
    
    return len(all_signals), len(buy_signals), len(sell_signals)


def test_alpha_trend_is_entry_is_exit():
    """测试AlphaTrendSignal的is_entry和is_exit方法，并保存数据到CSV"""
    print("获取真实K线数据进行entry/exit测试...")
    klines = fetch_klines_from_ccxt('BTC/USDT', '5m', 500)
    
    # 创建不同方向的信号
    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    sell_signal = AlphaTrendSignal(OrderSide.SELL)
    
    # 收集entry/exit信号数据
    entry_exit_data = []
    
    print(f"开始分析{len(klines)}根K线，测试entry/exit方法...")
    
    for i in range(50, len(klines)):
        current_data = klines.iloc[:i+1].copy()
        
        # 测试买入信号的entry和exit
        buy_is_entry = buy_signal.is_entry(current_data)
        buy_is_exit = buy_signal.is_exit(current_data)
        buy_run_result = buy_signal.run(current_data)
        
        # 测试卖出信号的entry和exit
        sell_is_entry = sell_signal.is_entry(current_data)
        sell_is_exit = sell_signal.is_exit(current_data)
        sell_run_result = sell_signal.run(current_data)
        
        # 获取当前价格和时间
        current_row = current_data.iloc[-1]
        
        # 计算alpha_trend值
        from strategy.alpha_trend_signal.alpha_trend_signal import _alpha_trend_indicator
        processed_data = _alpha_trend_indicator(current_data.copy())
        alpha_trend_value = processed_data.iloc[-1]['alpha_trend'] if pd.notna(processed_data.iloc[-1]['alpha_trend']) else None
        
        entry_exit_data.append({
            'datetime': current_row['datetime'],
            'price': current_row['close'],
            'alpha_trend': alpha_trend_value,
            'buy_run_signal': buy_run_result,
            'buy_is_entry': buy_is_entry,
            'buy_is_exit': buy_is_exit,
            'sell_run_signal': sell_run_result,
            'sell_is_entry': sell_is_entry,
            'sell_is_exit': sell_is_exit,
            'index': i
        })
    
    # 转换为DataFrame
    entry_exit_df = pd.DataFrame(entry_exit_data)
    
    # 保存完整的entry/exit数据
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    entry_exit_file = f'data/alpha_trend_entry_exit_signals_{timestamp}.csv'
    entry_exit_df.to_csv(entry_exit_file, index=False)
    print(f"已保存entry/exit信号数据到: {entry_exit_file}")
    
    # 分析并保存买入策略的entry/exit信号
    buy_entries = entry_exit_df[entry_exit_df['buy_is_entry'] == True].copy()
    buy_exits = entry_exit_df[entry_exit_df['buy_is_exit'] == True].copy()
    
    if not buy_entries.empty:
        buy_entries['signal_type'] = 'BUY_ENTRY'
        buy_entries_file = f'data/alpha_trend_buy_entries_{timestamp}.csv'
        buy_entries.to_csv(buy_entries_file, index=False)
        print(f"已保存{len(buy_entries)}个买入入场信号到: {buy_entries_file}")
    
    if not buy_exits.empty:
        buy_exits['signal_type'] = 'BUY_EXIT'
        buy_exits_file = f'data/alpha_trend_buy_exits_{timestamp}.csv'
        buy_exits.to_csv(buy_exits_file, index=False)
        print(f"已保存{len(buy_exits)}个买入出场信号到: {buy_exits_file}")
    
    # 分析并保存卖出策略的entry/exit信号
    sell_entries = entry_exit_df[entry_exit_df['sell_is_entry'] == True].copy()
    sell_exits = entry_exit_df[entry_exit_df['sell_is_exit'] == True].copy()
    
    if not sell_entries.empty:
        sell_entries['signal_type'] = 'SELL_ENTRY'
        sell_entries_file = f'data/alpha_trend_sell_entries_{timestamp}.csv'
        sell_entries.to_csv(sell_entries_file, index=False)
        print(f"已保存{len(sell_entries)}个卖出入场信号到: {sell_entries_file}")
    
    if not sell_exits.empty:
        sell_exits['signal_type'] = 'SELL_EXIT'
        sell_exits_file = f'data/alpha_trend_sell_exits_{timestamp}.csv'
        sell_exits.to_csv(sell_exits_file, index=False)
        print(f"已保存{len(sell_exits)}个卖出出场信号到: {sell_exits_file}")
    
    # 创建交易对分析
    buy_trades = []
    sell_trades = []
    
    # 分析买入策略的交易对（买入entry -> 卖出exit）
    current_buy_position = None
    for _, row in entry_exit_df.iterrows():
        if row['buy_is_entry'] and current_buy_position is None:
            current_buy_position = {
                'entry_time': row['datetime'],
                'entry_price': row['price'],
                'entry_alpha_trend': row['alpha_trend'],
                'entry_index': row['index']
            }
        elif row['buy_is_exit'] and current_buy_position is not None:
            profit = row['price'] - current_buy_position['entry_price']
            profit_rate = profit / current_buy_position['entry_price']
            
            buy_trades.append({
                'strategy': 'BUY',
                'entry_time': current_buy_position['entry_time'],
                'exit_time': row['datetime'],
                'entry_price': current_buy_position['entry_price'],
                'exit_price': row['price'],
                'entry_alpha_trend': current_buy_position['entry_alpha_trend'],
                'exit_alpha_trend': row['alpha_trend'],
                'profit': profit,
                'profit_rate': profit_rate,
                'hold_periods': row['index'] - current_buy_position['entry_index']
            })
            current_buy_position = None
    
    # 分析卖出策略的交易对（卖出entry -> 买入exit）
    current_sell_position = None
    for _, row in entry_exit_df.iterrows():
        if row['sell_is_entry'] and current_sell_position is None:
            current_sell_position = {
                'entry_time': row['datetime'],
                'entry_price': row['price'],
                'entry_alpha_trend': row['alpha_trend'],
                'entry_index': row['index']
            }
        elif row['sell_is_exit'] and current_sell_position is not None:
            profit = current_sell_position['entry_price'] - row['price']  # 卖空盈利计算
            profit_rate = profit / current_sell_position['entry_price']
            
            sell_trades.append({
                'strategy': 'SELL',
                'entry_time': current_sell_position['entry_time'],
                'exit_time': row['datetime'],
                'entry_price': current_sell_position['entry_price'],
                'exit_price': row['price'],
                'entry_alpha_trend': current_sell_position['entry_alpha_trend'],
                'exit_alpha_trend': row['alpha_trend'],
                'profit': profit,
                'profit_rate': profit_rate,
                'hold_periods': row['index'] - current_sell_position['entry_index']
            })
            current_sell_position = None
    
    # 保存交易对数据
    all_trades = buy_trades + sell_trades
    if all_trades:
        trades_df = pd.DataFrame(all_trades)
        trades_file = f'data/alpha_trend_entry_exit_trades_{timestamp}.csv'
        trades_df.to_csv(trades_file, index=False)
        print(f"已保存{len(all_trades)}个完整交易对到: {trades_file}")
    
    # 打印统计信息
    print(f"\n=== Entry/Exit 测试结果 ===")
    print(f"买入入场信号: {len(buy_entries)}")
    print(f"买入出场信号: {len(buy_exits)}")
    print(f"卖出入场信号: {len(sell_entries)}")
    print(f"卖出出场信号: {len(sell_exits)}")
    print(f"买入策略完整交易: {len(buy_trades)}")
    print(f"卖出策略完整交易: {len(sell_trades)}")
    
    if buy_trades:
        buy_trades_df = pd.DataFrame(buy_trades)
        buy_win_rate = len(buy_trades_df[buy_trades_df['profit'] > 0]) / len(buy_trades_df)
        buy_avg_return = buy_trades_df['profit_rate'].mean()
        print(f"买入策略胜率: {buy_win_rate*100:.2f}%")
        print(f"买入策略平均收益率: {buy_avg_return*100:.4f}%")
    
    if sell_trades:
        sell_trades_df = pd.DataFrame(sell_trades)
        sell_win_rate = len(sell_trades_df[sell_trades_df['profit'] > 0]) / len(sell_trades_df)
        sell_avg_return = sell_trades_df['profit_rate'].mean()
        print(f"卖出策略胜率: {sell_win_rate*100:.2f}%")
        print(f"卖出策略平均收益率: {sell_avg_return*100:.4f}%")
    
    # 显示最近的信号
    if not buy_entries.empty:
        print(f"\n最近的买入入场信号:")
        for _, signal in buy_entries.tail(3).iterrows():
            print(f"  {signal['datetime']} - 价格: {signal['price']:.2f}")
    
    if not sell_entries.empty:
        print(f"\n最近的卖出入场信号:")
        for _, signal in sell_entries.tail(3).iterrows():
            print(f"  {signal['datetime']} - 价格: {signal['price']:.2f}")
    
    # 验证is_entry和is_exit逻辑的一致性
    buy_entry_count = len(entry_exit_df[entry_exit_df['buy_is_entry'] == True])
    buy_signal_1_count = len(entry_exit_df[entry_exit_df['buy_run_signal'] == 1])
    
    sell_entry_count = len(entry_exit_df[entry_exit_df['sell_is_entry'] == True])
    sell_signal_minus1_count = len(entry_exit_df[entry_exit_df['sell_run_signal'] == -1])
    
    assert buy_entry_count == buy_signal_1_count, f"买入is_entry数量({buy_entry_count})应该等于run信号=1的数量({buy_signal_1_count})"
    assert sell_entry_count == sell_signal_minus1_count, f"卖出is_entry数量({sell_entry_count})应该等于run信号=-1的数量({sell_signal_minus1_count})"
    
    print(f"\n✅ is_entry/is_exit 逻辑验证通过")
    
    return len(entry_exit_data), len(buy_entries), len(buy_exits), len(sell_entries), len(sell_exits)


def test_alpha_trend_signal_backtest():
    """回测AlphaTrendSignal信号效果"""
    print("获取真实K线数据进行回测...")
    klines = fetch_klines_from_ccxt('BTC/USDT', '5m', 500)  # 使用5分钟K线
    periods = len(klines)
    
    # 测试买入和卖出信号
    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    sell_signal = AlphaTrendSignal(OrderSide.SELL)
    
    buy_signals = []
    sell_signals = []
    
    print(f"开始回测，总共{periods}根K线...")
    
    # 逐行回测，模拟实时信号生成
    for i in range(50, len(klines)):  # 从第50行开始，确保有足够的历史数据
        current_data = klines.iloc[:i+1].copy()
        
        buy_result = buy_signal.run(current_data)
        sell_result = sell_signal.run(current_data)
        
        buy_signals.append({
            'datetime': current_data.iloc[-1]['datetime'],
            'price': current_data.iloc[-1]['close'],
            'signal': buy_result,
            'index': i
        })
        
        sell_signals.append({
            'datetime': current_data.iloc[-1]['datetime'],
            'price': current_data.iloc[-1]['close'],
            'signal': sell_result,
            'index': i
        })
    
    # 分析信号统计
    buy_df = pd.DataFrame(buy_signals)
    sell_df = pd.DataFrame(sell_signals)
    
    buy_count = len(buy_df[buy_df['signal'] == 1])
    sell_count = len(sell_df[sell_df['signal'] == -1])
    
    print("\n回测结果:")
    print(f"数据来源: BTC/USDT 5分钟K线")
    print(f"总测试周期: {periods}个K线")
    print(f"价格范围: {klines['close'].min():.2f} - {klines['close'].max():.2f}")
    print(f"买入信号次数: {buy_count}")
    print(f"卖出信号次数: {sell_count}")
    print(f"买入信号频率: {buy_count/(len(buy_df))*100:.2f}%")
    print(f"卖出信号频率: {sell_count/(len(sell_df))*100:.2f}%")
    
    # 显示最近的信号
    recent_buy_signals = buy_df[buy_df['signal'] == 1].tail(3)
    recent_sell_signals = sell_df[sell_df['signal'] == -1].tail(3)
    
    if not recent_buy_signals.empty:
        print("\n最近的买入信号:")
        for _, signal in recent_buy_signals.iterrows():
            print(f"  {signal['datetime']} - 价格: {signal['price']:.2f}")
    
    if not recent_sell_signals.empty:
        print("\n最近的卖出信号:")
        for _, signal in recent_sell_signals.iterrows():
            print(f"  {signal['datetime']} - 价格: {signal['price']:.2f}")
    
    # 验证信号有效性
    assert buy_count + sell_count > 0, "应该产生一些交易信号"


def test_alpha_trend_signal_performance():
    """测试信号的盈利性能"""
    print("获取真实K线数据进行性能测试...")
    klines = fetch_klines_from_ccxt('BTC/USDT', '15m', 1000)  # 使用15分钟K线获得更多数据
    
    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    
    trades = []
    current_position = None
    
    print(f"开始性能分析，数据范围: {klines['datetime'].iloc[0]} 到 {klines['datetime'].iloc[-1]}")
    
    for i in range(50, len(klines)):
        current_data = klines.iloc[:i+1].copy()
        signal = buy_signal.run(current_data)
        current_price = current_data.iloc[-1]['close']
        current_time = current_data.iloc[-1]['datetime']
        
        if signal == 1 and current_position is None:  # 买入信号
            current_position = {
                'entry_time': current_time,
                'entry_price': current_price,
                'entry_index': i
            }
        elif signal == -1 and current_position is not None:  # 卖出信号
            profit = current_price - current_position['entry_price']
            profit_rate = profit / current_position['entry_price']
            
            trades.append({
                'entry_time': current_position['entry_time'],
                'exit_time': current_time,
                'entry_price': current_position['entry_price'],
                'exit_price': current_price,
                'profit': profit,
                'profit_rate': profit_rate,
                'hold_periods': i - current_position['entry_index']
            })
            
            current_position = None
    
    if trades:
        trades_df = pd.DataFrame(trades)
        
        total_trades = len(trades_df)
        profitable_trades = len(trades_df[trades_df['profit'] > 0])
        win_rate = profitable_trades / total_trades if total_trades > 0 else 0
        avg_profit_rate = trades_df['profit_rate'].mean()
        total_return = trades_df['profit_rate'].sum()
        max_profit = trades_df['profit_rate'].max()
        max_loss = trades_df['profit_rate'].min()
        
        print("\n交易性能分析:")
        print(f"数据来源: BTC/USDT 15分钟K线")
        print(f"总交易次数: {total_trades}")
        print(f"盈利交易次数: {profitable_trades}")
        print(f"胜率: {win_rate*100:.2f}%")
        print(f"平均收益率: {avg_profit_rate*100:.4f}%")
        print(f"累计收益率: {total_return*100:.4f}%")
        print(f"最大单次盈利: {max_profit*100:.4f}%")
        print(f"最大单次亏损: {max_loss*100:.4f}%")
        print(f"平均持仓周期: {trades_df['hold_periods'].mean():.1f}个K线")
        
        # 显示前几次交易详情
        print("\n前5次交易详情:")
        for i, trade in trades_df.head(5).iterrows():
            print(f"  {trade['entry_time']} -> {trade['exit_time']}")
            print(f"  价格: {trade['entry_price']:.2f} -> {trade['exit_price']:.2f}")
            print(f"  收益: {trade['profit_rate']*100:.4f}% ({trade['hold_periods']}个周期)")
            print()
        
        # 基本的性能验证
        assert total_trades > 1, "应该有足够的交易样本"
        print(f"性能测试完成，共完成{total_trades}次交易")
    else:
        print("\n未产生完整交易对")


def test_alpha_trend_signal_parameters():
    """测试不同参数对信号的影响"""
    print("获取真实K线数据进行参数测试...")
    klines = fetch_klines_from_ccxt('BTC/USDT', '1h', 500)  # 使用1小时K线
    
    # 测试不同的ATR倍数
    atr_multiples = [0.5, 1.0, 2.0]
    periods = [8, 14, 21]
    
    results = []
    
    print("测试不同参数组合的信号生成效果...")
    
    for atr_mult in atr_multiples:
        for period in periods:
            signal = AlphaTrendSignal(OrderSide.BUY, atr_multiple=atr_mult, period=period)
            
            signal_count = 0
            last_signals = []
            
            for i in range(50, len(klines)):
                current_data = klines.iloc[:i+1].copy()
                result = signal.run(current_data)
                if result != 0:
                    signal_count += 1
                    # 记录最后几个信号
                    if len(last_signals) < 3:
                        last_signals.append({
                            'time': current_data.iloc[-1]['datetime'],
                            'price': current_data.iloc[-1]['close'],
                            'signal': result
                        })
            
            results.append({
                'atr_multiple': atr_mult,
                'period': period,
                'signal_count': signal_count,
                'signal_rate': signal_count / (len(klines) - 50) * 100,
                'avg_price': klines['close'].mean()
            })
    
    results_df = pd.DataFrame(results)
    print("\n参数影响分析:")
    print(f"数据来源: BTC/USDT 1小时K线")
    print(f"测试K线数量: {len(klines)}")
    print(f"价格范围: {klines['close'].min():.2f} - {klines['close'].max():.2f}")
    print("\n各参数组合的信号统计:")
    for _, row in results_df.iterrows():
        print(f"ATR倍数: {row['atr_multiple']}, 周期: {row['period']} -> "
              f"信号次数: {row['signal_count']}, 频率: {row['signal_rate']:.2f}%")
    
    # 验证参数确实影响信号生成
    signal_counts = results_df['signal_count'].values
    assert len(set(signal_counts)) > 1, "不同参数应该产生不同数量的信号"
    print(f"\n参数测试完成，共测试{len(results)}种参数组合")


if __name__ == "__main__":
    # 运行entry/exit测试并生成CSV文件
    print("=== 开始 AlphaTrendSignal Entry/Exit 测试 ===\n")
    
    total_points, buy_entries, buy_exits, sell_entries, sell_exits = test_alpha_trend_is_entry_is_exit()
    
    print(f"\n=== Entry/Exit 测试完成 ===")
    print(f"总数据点: {total_points}")
    print(f"买入入场信号: {buy_entries}")
    print(f"买入出场信号: {buy_exits}")
    print(f"卖出入场信号: {sell_entries}")
    print(f"卖出出场信号: {sell_exits}")
    print("详细的Entry/Exit CSV文件已保存到data目录")
