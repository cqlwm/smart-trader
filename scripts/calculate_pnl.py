import json
import os
import argparse

def calculate_pnl(file_path):
    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    history_orders = data.get('history_orders', [])
    
    if not history_orders:
        print("No history orders found in the file.")
        return

    total_profit = 0.0
    winning_trades = 0
    losing_trades = 0
    breakeven_trades = 0
    total_volume = 0.0
    
    max_profit = float('-inf')
    max_loss = float('inf')
    profits = []

    for order in history_orders:
        if order.get('status') != 'closed':
            continue

        side = order.get('side', '').lower()
        entry_price = float(order.get('price', 0.0))
        exit_price = float(order.get('exit_price', 0.0))
        quantity = float(order.get('quantity', 0.0))

        if side == 'buy':
            profit = (exit_price - entry_price) * quantity
        elif side == 'sell':
            profit = (entry_price - exit_price) * quantity
        else:
            continue

        profits.append(profit)
        total_profit += profit
        total_volume += entry_price * quantity

        if profit > 0:
            winning_trades += 1
        elif profit < 0:
            losing_trades += 1
        else:
            breakeven_trades += 1
            
        if profit > max_profit:
            max_profit = profit
        if profit < max_loss:
            max_loss = profit

    total_trades = winning_trades + losing_trades + breakeven_trades
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    avg_profit = sum(p for p in profits if p > 0) / winning_trades if winning_trades > 0 else 0.0
    avg_loss = sum(p for p in profits if p < 0) / losing_trades if losing_trades > 0 else 0.0
    avg_trade = total_profit / total_trades if total_trades > 0 else 0.0

    print("=" * 40)
    print(f"PnL Analysis: {os.path.basename(file_path)}")
    print("=" * 40)
    print(f"Total Trades:       {total_trades}")
    print(f"Winning Trades:     {winning_trades}")
    print(f"Losing Trades:      {losing_trades}")
    print(f"Breakeven Trades:   {breakeven_trades}")
    print(f"Win Rate:           {win_rate:.2f}%")
    print("-" * 40)
    print(f"Total Volume:       {total_volume:.2f} USDT")
    print(f"Total Profit:       {total_profit:.4f} USDT")
    print("-" * 40)
    print(f"Max Profit:         {max_profit:.4f} USDT" if max_profit != float('-inf') else "Max Profit:         N/A")
    print(f"Max Loss:           {max_loss:.4f} USDT" if max_loss != float('inf') else "Max Loss:           N/A")
    print(f"Average Profit:     {avg_profit:.4f} USDT")
    print(f"Average Loss:       {avg_loss:.4f} USDT")
    print(f"Average per Trade:  {avg_trade:.4f} USDT")
    print("=" * 40)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate PnL from order history JSON")
    parser.add_argument(
        "--file", 
        type=str, 
        default="data/signal_grid_long_buy_DOGEUSDT_1m.json",
        help="Path to the JSON file"
    )
    args = parser.parse_args()
    
    calculate_pnl(args.file)
