import sys
import os
import dotenv
import ccxt

dotenv.load_dotenv()

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python test_query_order.py <order_id> <symbol>")
        print("Example: python test_query_order.py my_order_123 BTCUSDT")
        sys.exit(1)

    order_id = sys.argv[1]
    symbol_str = sys.argv[2]

    # Get API credentials from environment
    api_key = os.environ.get('BINANCE_API_KEY_COPY')
    api_secret = os.environ.get('BINANCE_API_SECRET_COPY')
    is_test = os.environ.get('BINANCE_IS_TEST_COPY') == 'True'

    if not api_key or not api_secret:
        raise ValueError('BINANCE_API_KEY and BINANCE_API_SECRET must be set in environment variables')

    print(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

    # Create Binance client using ccxt
    exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': api_secret,
        'options': {
            'defaultType': 'future',
        }
    })

    if is_test:
        exchange.set_sandbox_mode(True)

    try:
        # Query the order using ccxt
        order = exchange.fetch_order(id='', symbol=symbol_str.upper(), params={
            'origClientOrderId': order_id
        })

        # Print the order information
        print(f"Order ID: {order_id}")
        print(f"Symbol: {symbol_str}")
        print("Order Details:")
        print(f"  Status: {order.get('status', 'Unknown')}")
        print(f"  Side: {order.get('side', 'Unknown')}")
        print(f"  Type: {order.get('type', 'Unknown')}")
        print(f"  Price: {order.get('price', 'Unknown')}")
        print(f"  Amount: {order.get('amount', 'Unknown')}")
        print(f"  Filled: {order.get('filled', 'Unknown')}")
        print(f"  Remaining: {order.get('remaining', 'Unknown')}")
        print(f"  Cost: {order.get('cost', 'Unknown')}")

        # Print full order response for debugging
        print("\nFull Order Response:")
        print(order)

    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)
