import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
import log
from datetime import datetime

logger = log.getLogger(__name__)


class BacktestAnalyzer:
    """
    回测结果分析器
    """

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance

    def analyze(self, trade_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析交易历史，返回各项指标
        """
        if not trade_history:
            logger.warning("No trade history to analyze")
            return self._empty_results()

        # 转换为DataFrame便于分析
        df = pd.DataFrame(trade_history)

        # 确保数据类型正确
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['filled_price'] = df['filled_price'].astype(float)
        df['filled_quantity'] = df['filled_quantity'].astype(float)
        df['fee'] = df['fee'].astype(float)

        # 计算收益
        total_return = self._calculate_total_return(df)
        annualized_return = self._calculate_annualized_return(df, total_return)

        # 计算风险指标
        volatility = self._calculate_volatility(df)
        max_drawdown = self._calculate_max_drawdown(df)
        sharpe_ratio = self._calculate_sharpe_ratio(total_return, volatility)

        # 计算交易统计
        win_rate = self._calculate_win_rate(df)
        profit_factor = self._calculate_profit_factor(df)
        avg_trade = self._calculate_avg_trade(df)

        # 计算其他指标
        total_trades = len(df)
        total_fees = df['fee'].sum()
        best_trade = df['filled_price'].max() if not df.empty else 0
        worst_trade = df['filled_price'].min() if not df.empty else 0

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
            'equity_curve': self._calculate_equity_curve(df),
            'monthly_returns': self._calculate_monthly_returns(df),
            'trade_analysis': self._analyze_trades(df)
        }

    def _empty_results(self) -> Dict[str, Any]:
        """返回空结果"""
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

    def _calculate_total_return(self, df: pd.DataFrame) -> float:
        """计算总收益"""
        if df.empty:
            return 0.0

        # 简单起见，假设所有交易都是买入并在某个时候卖出
        # 这里需要根据实际的交易逻辑来计算
        # 暂时用成交总额的简单计算
        total_value = (df['filled_price'] * df['filled_quantity']).sum()
        return total_value - self.initial_balance

    def _calculate_annualized_return(self, df: pd.DataFrame, total_return: float) -> float:
        """计算年化收益率"""
        if df.empty:
            return 0.0

        # 计算持有期（天）
        start_date = df['timestamp'].min()
        end_date = df['timestamp'].max()
        days = (end_date - start_date).days

        if days <= 0:
            return 0.0

        # 年化收益率 = (1 + 总收益率)^(365/持有天数) - 1
        total_return_rate = total_return / self.initial_balance
        if total_return_rate >= -1:  # 避免负数开根号
            annualized = (1 + total_return_rate) ** (365 / days) - 1
        else:
            annualized = -1.0

        return annualized

    def _calculate_volatility(self, df: pd.DataFrame) -> float:
        """计算波动率"""
        if df.empty or len(df) < 2:
            return 0.0

        # 计算每日收益率的波动率
        # 这里简化处理，假设交易均匀分布
        returns = df['filled_price'].pct_change().dropna()
        if returns.empty:
            return 0.0

        return returns.std() * np.sqrt(365)  # 年化波动率

    def _calculate_max_drawdown(self, df: pd.DataFrame) -> float:
        """计算最大回撤"""
        if df.empty:
            return 0.0

        # 计算累积收益
        cumulative = (1 + df['filled_price'].pct_change().fillna(0)).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max

        return drawdown.min() * self.initial_balance

    def _calculate_sharpe_ratio(self, total_return: float, volatility: float, risk_free_rate: float = 0.02) -> float:
        """计算夏普比率"""
        if volatility == 0:
            return 0.0

        excess_return = (total_return / self.initial_balance) - risk_free_rate
        return excess_return / volatility

    def _calculate_win_rate(self, df: pd.DataFrame) -> float:
        """计算胜率"""
        if df.empty:
            return 0.0

        # 假设盈利交易为胜
        winning_trades = (df['filled_price'] > 0).sum()
        return winning_trades / len(df)

    def _calculate_profit_factor(self, df: pd.DataFrame) -> float:
        """计算盈利因子"""
        if df.empty:
            return 0.0

        profits = df[df['filled_price'] > 0]['filled_price'].sum()
        losses = abs(df[df['filled_price'] <= 0]['filled_price'].sum())

        if losses == 0:
            return float('inf')

        return profits / losses

    def _calculate_avg_trade(self, df: pd.DataFrame) -> float:
        """计算平均交易收益"""
        if df.empty:
            return 0.0

        return df['filled_price'].mean()

    def _calculate_equity_curve(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """计算权益曲线"""
        if df.empty:
            return []

        # 计算累积收益
        cumulative_returns = (1 + df['filled_price'].pct_change().fillna(0)).cumprod()
        equity = self.initial_balance * cumulative_returns

        curve = []
        for i, (timestamp, eq) in enumerate(zip(df['timestamp'], equity)):
            curve.append({
                'timestamp': timestamp.timestamp() * 1000,
                'equity': eq,
                'return': df.iloc[i]['filled_price']
            })

        return curve

    def _calculate_monthly_returns(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """计算月度收益"""
        if df.empty:
            return []

        # 按月分组计算收益
        df['month'] = df['timestamp'].dt.to_period('M')
        monthly = df.groupby('month')['filled_price'].sum()

        returns = []
        for period, return_val in monthly.items():
            returns.append({
                'month': str(period),
                'return': return_val,
                'return_pct': (return_val / self.initial_balance) * 100
            })

        return returns

    def _analyze_trades(self, df: pd.DataFrame) -> Dict[str, Any]:
        """分析交易详情"""
        if df.empty:
            return {}

        return {
            'largest_win': df['filled_price'].max(),
            'largest_loss': df['filled_price'].min(),
            'avg_win': df[df['filled_price'] > 0]['filled_price'].mean(),
            'avg_loss': df[df['filled_price'] <= 0]['filled_price'].mean(),
            'trade_count': len(df),
            'profitable_trades': (df['filled_price'] > 0).sum(),
            'losing_trades': (df['filled_price'] <= 0).sum()
        }

    def generate_report(self, analysis_results: Dict[str, Any], output_file: Optional[str] = None) -> str:
        """生成回测报告"""
        report = []
        report.append("=" * 50)
        report.append("BACKTEST REPORT")
        report.append("=" * 50)

        # 汇总信息
        summary = analysis_results['summary']
        report.append("\nSUMMARY:")
        report.append(f"Total Trades: {summary['total_trades']}")
        report.append(f"Total Return: ${summary['total_return']:.2f}")
        report.append(f"Total Return %: {summary['total_return_pct']:.2f}%")
        report.append(f"Annualized Return: {summary['annualized_return']:.2f}")
        report.append(f"Annualized Return %: {summary['annualized_return_pct']:.2f}%")
        report.append(f"Total Fees: ${summary['total_fees']:.2f}")
        report.append(f"Net Return: ${summary['net_return']:.2f}")

        # 风险指标
        risk = analysis_results['risk_metrics']
        report.append("\nRISK METRICS:")
        report.append(f"Volatility: {risk['volatility']:.4f}")
        report.append(f"Max Drawdown: ${risk['max_drawdown']:.2f}")
        report.append(f"Max Drawdown %: {risk['max_drawdown_pct']:.2f}%")
        report.append(f"Sharpe Ratio: {risk['sharpe_ratio']:.2f}")

        # 交易指标
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
