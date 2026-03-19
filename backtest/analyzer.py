import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
import log

logger = log.getLogger(__name__)


class BacktestAnalyzer:
    """回测结果分析器"""

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance

    def analyze(self, trade_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not trade_history:
            logger.warning("No trade history to analyze")
            return self._empty_results()

        df = pd.DataFrame(trade_history)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['filled_price'] = df['filled_price'].astype(float)
        df['filled_quantity'] = df['filled_quantity'].astype(float)
        df['fee'] = df['fee'].astype(float)

        completed_trades = self._identify_completed_trades(df)

        if not completed_trades:
            logger.warning("No completed trades found in order history")
            return self._empty_results()

        trades_df = pd.DataFrame(completed_trades)

        total_return = trades_df['pnl'].sum()
        annualized_return = self._calculate_annualized_return(trades_df, total_return)
        volatility = self._calculate_volatility_from_trades(trades_df)
        max_drawdown = self._calculate_max_drawdown_from_trades(trades_df)
        sharpe_ratio = self._calculate_sharpe_ratio(annualized_return, volatility)
        win_rate = self._calculate_win_rate_from_trades(trades_df)
        profit_factor = self._calculate_profit_factor_from_trades(trades_df)
        avg_trade = self._calculate_avg_trade_from_trades(trades_df)

        total_trades = len(trades_df)
        total_fees = trades_df['total_fees'].sum()
        best_trade = trades_df['pnl'].max() if not trades_df.empty else 0
        worst_trade = trades_df['pnl'].min() if not trades_df.empty else 0

        return {
            'summary': {
                'total_trades': total_trades,
                'total_return': total_return,
                'total_return_pct': (total_return / self.initial_balance) * 100,
                'annualized_return': annualized_return,
                'annualized_return_pct': annualized_return * 100,
                'total_fees': total_fees,
                'net_return': total_return - total_fees
            },
            'risk_metrics': {
                'volatility': volatility,
                'max_drawdown': max_drawdown,
                'max_drawdown_pct': (max_drawdown / self.initial_balance) * 100,
                'sharpe_ratio': sharpe_ratio
            },
            'trade_metrics': {
                'win_rate': win_rate,
                'win_rate_pct': win_rate * 100,
                'profit_factor': profit_factor,
                'avg_trade_return': avg_trade,
                'best_trade': best_trade,
                'worst_trade': worst_trade
            },
            'equity_curve': self._calculate_equity_curve_from_trades(trades_df),
            'monthly_returns': self._calculate_monthly_returns_from_trades(trades_df),
            'trade_analysis': self._analyze_completed_trades(trades_df)
        }

    def _empty_results(self) -> Dict[str, Any]:
        return {
            'summary': {
                'total_trades': 0,
                'total_return': 0.0,
                'total_return_pct': 0.0,
                'annualized_return': 0.0,
                'annualized_return_pct': 0.0,
                'total_fees': 0.0,
                'net_return': 0.0
            },
            'risk_metrics': {
                'volatility': 0.0,
                'max_drawdown': 0.0,
                'max_drawdown_pct': 0.0,
                'sharpe_ratio': 0.0
            },
            'trade_metrics': {
                'win_rate': 0.0,
                'win_rate_pct': 0.0,
                'profit_factor': 0.0,
                'avg_trade_return': 0.0,
                'best_trade': 0.0,
                'worst_trade': 0.0
            },
            'equity_curve': [],
            'monthly_returns': [],
            'trade_analysis': {}
        }

    def _identify_completed_trades(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        识别已完成交易，基于 custom_id 命名约定配对开平仓订单。

        约定：开仓订单 id = X，对应平仓订单 id = exit_X（即 Order.exit_id()）。
        对于无法通过命名配对的订单，回退到按品种+方向的 FIFO 堆栈配对。
        """
        completed_trades = []
        df_sorted = df.sort_values('timestamp').reset_index(drop=True)

        # 尝试 exit_id 命名约定配对
        exit_prefix = 'exit_'
        exit_ids = set(df_sorted[df_sorted['id'].str.startswith(exit_prefix, na=False)]['id'])
        matched_entry_ids: set = set()
        matched_exit_ids: set = set()

        for _, exit_row in df_sorted[df_sorted['id'].str.startswith(exit_prefix, na=False)].iterrows():
            entry_id = exit_row['id'][len(exit_prefix):]
            entry_rows = df_sorted[df_sorted['id'] == entry_id]
            if entry_rows.empty:
                continue
            entry_row = entry_rows.iloc[0]
            trade = self._make_trade(entry_row, exit_row)
            if trade:
                completed_trades.append(trade)
                matched_entry_ids.add(entry_id)
                matched_exit_ids.add(exit_row['id'])

        # 未配对订单使用 FIFO 堆栈按 (symbol, position_side) 配对
        unmatched = df_sorted[
            ~df_sorted['id'].isin(matched_entry_ids | matched_exit_ids)
        ]

        stacks: Dict[str, List] = {}
        for _, row in unmatched.iterrows():
            key = f"{row['symbol']}_{row['position_side']}"
            stacks.setdefault(key, [])
            side = row.get('side', '')
            # 开仓：long+BUY 或 short+SELL；平仓：long+SELL 或 short+BUY
            is_open = (row['position_side'] == 'long' and side == 'buy') or \
                      (row['position_side'] == 'short' and side == 'sell')
            if is_open:
                stacks[key].append(row)
            elif stacks[key]:
                entry_row = stacks[key].pop(0)
                trade = self._make_trade(entry_row, row)
                if trade:
                    completed_trades.append(trade)

        # 按开仓时间排序
        completed_trades.sort(key=lambda t: t['entry_time'])
        logger.info(f"Identified {len(completed_trades)} completed trades")
        return completed_trades

    def _make_trade(self, entry_row: Any, exit_row: Any) -> Optional[Dict[str, Any]]:
        position_side = entry_row['position_side']
        if position_side == 'long':
            pnl = (exit_row['filled_price'] - entry_row['filled_price']) * entry_row['filled_quantity']
        elif position_side == 'short':
            pnl = (entry_row['filled_price'] - exit_row['filled_price']) * entry_row['filled_quantity']
        else:
            return None

        total_fees = entry_row['fee'] + exit_row['fee']
        return {
            'symbol': entry_row['symbol'],
            'position_side': position_side,
            'entry_price': entry_row['filled_price'],
            'exit_price': exit_row['filled_price'],
            'quantity': entry_row['filled_quantity'],
            'pnl': pnl,
            'total_fees': total_fees,
            'net_pnl': pnl - total_fees,
            'entry_time': entry_row['timestamp'],
            'exit_time': exit_row['timestamp']
        }

    def _calculate_annualized_return(self, df: pd.DataFrame, total_return: float) -> float:
        if df.empty:
            return 0.0
        start_date = df['entry_time'].min()
        end_date = df['exit_time'].max()
        days = (end_date - start_date).days
        if days <= 0:
            return 0.0
        total_return_rate = total_return / self.initial_balance
        if total_return_rate >= -1:
            return (1 + total_return_rate) ** (365 / days) - 1
        return -1.0

    def _calculate_volatility_from_trades(self, df: pd.DataFrame) -> float:
        """年化波动率：基于每笔交易净收益率序列的标准差"""
        if df.empty or len(df) < 2:
            return 0.0
        # 每笔交易收益率 = net_pnl / initial_balance
        returns = df['net_pnl'] / self.initial_balance
        if returns.std() == 0:
            return 0.0
        # 假设平均每笔交易相当于一天（年化用 sqrt(252)）
        return returns.std() * np.sqrt(252)

    def _calculate_max_drawdown_from_trades(self, df: pd.DataFrame) -> float:
        """最大回撤：用累积盈亏构造权益曲线计算"""
        if df.empty:
            return 0.0
        df_sorted = df.sort_values('exit_time')
        equity = self.initial_balance + df_sorted['pnl'].cumsum()
        running_max = equity.expanding().max()
        drawdown = equity - running_max
        return abs(drawdown.min())

    def _calculate_sharpe_ratio(self, annualized_return: float, volatility: float,
                                risk_free_rate: float = 0.02) -> float:
        """夏普比率：(年化收益率 - 无风险利率) / 年化波动率"""
        if volatility == 0:
            return 0.0
        return (annualized_return - risk_free_rate) / volatility

    def _calculate_win_rate_from_trades(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        return (df['pnl'] > 0).sum() / len(df)

    def _calculate_profit_factor_from_trades(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        profits = df[df['pnl'] > 0]['pnl'].sum()
        losses = abs(df[df['pnl'] <= 0]['pnl'].sum())
        if losses == 0:
            return float('inf')
        return profits / losses

    def _calculate_avg_trade_from_trades(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        return df['pnl'].mean()

    def _calculate_equity_curve_from_trades(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df.empty:
            return []
        df_sorted = df.sort_values('exit_time')
        cumulative_pnl = df_sorted['pnl'].cumsum()
        equity = self.initial_balance + cumulative_pnl

        return [
            {
                'timestamp': ts.timestamp() * 1000 if hasattr(ts, 'timestamp') else ts,
                'equity': eq,
                'return': pnl
            }
            for ts, eq, pnl in zip(df_sorted['exit_time'], equity, df_sorted['pnl'])
        ]

    def _calculate_monthly_returns_from_trades(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        return []

    def _analyze_completed_trades(self, df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty:
            return {}
        return {
            'largest_win': df['pnl'].max(),
            'largest_loss': df['pnl'].min(),
            'avg_win': df[df['pnl'] > 0]['pnl'].mean() if (df['pnl'] > 0).any() else 0,
            'avg_loss': df[df['pnl'] <= 0]['pnl'].mean() if (df['pnl'] <= 0).any() else 0,
            'trade_count': len(df),
            'profitable_trades': (df['pnl'] > 0).sum(),
            'losing_trades': (df['pnl'] <= 0).sum()
        }

    def generate_report(self, analysis_results: Dict[str, Any], output_file: Optional[str] = None) -> str:
        report = []
        report.append("=" * 50)
        report.append("BACKTEST REPORT")
        report.append("=" * 50)

        summary = analysis_results['summary']
        report.append("\nSUMMARY:")
        report.append(f"Total Trades: {summary['total_trades']}")
        report.append(f"Total Return: ${summary['total_return']:.2f}")
        report.append(f"Total Return %: {summary['total_return_pct']:.2f}%")
        report.append(f"Annualized Return: {summary['annualized_return']:.2f}")
        report.append(f"Annualized Return %: {summary['annualized_return_pct']:.2f}%")
        report.append(f"Total Fees: ${summary['total_fees']:.2f}")
        report.append(f"Net Return: ${summary['net_return']:.2f}")

        risk = analysis_results['risk_metrics']
        report.append("\nRISK METRICS:")
        report.append(f"Volatility: {risk['volatility']:.4f}")
        report.append(f"Max Drawdown: ${risk['max_drawdown']:.2f}")
        report.append(f"Max Drawdown %: {risk['max_drawdown_pct']:.2f}%")
        report.append(f"Sharpe Ratio: {risk['sharpe_ratio']:.2f}")

        trade = analysis_results['trade_metrics']
        report.append("\nTRADE METRICS:")
        report.append(f"Win Rate: {trade['win_rate_pct']:.2f}%")
        report.append(f"Profit Factor: {trade['profit_factor']:.4f}")
        report.append(f"Avg Trade Return: ${trade['avg_trade_return']:.2f}")
        report.append(f"Best Trade: ${trade['best_trade']:.2f}")
        report.append(f"Worst Trade: ${trade['worst_trade']:.2f}")

        report_str = "\n".join(report)

        if output_file:
            with open(output_file, 'w') as f:
                f.write(report_str)
            logger.info(f"Report saved to {output_file}")

        return report_str
