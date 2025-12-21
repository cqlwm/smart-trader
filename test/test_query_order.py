import sys
import os
import dotenv

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.binance_client import BinanceSwapClient
from model import Symbol
import log

dotenv.load_dotenv()

logger = log.getLogger(__name__)

def parse_symbol(symbol_str: str) -> Symbol:
    """Parse symbol string like 'BTCUSDT' into Symbol object"""
    # Assume format is BASEQUOTE, where base is everything except last 4-5 chars for quote
    # Common quotes are USDT, USDC, BUSD, etc.
    if symbol_str.upper().endswith(('USDT', 'USDC', 'BUSD', 'TUSD')):
        quote_len = 4
    elif symbol_str.upper().endswith(('BTC', 'ETH')):
        quote_len = 3
    else:
        # Default assumption: last 4 characters are quote
        quote_len = 4

    base = symbol_str[:-quote_len]
    quote = symbol_str[-quote_len:]
    return Symbol(base=base, quote=quote)

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

    logger.info(f'api_key: {api_key[:5]}*****, api_secret: {api_secret[:5]}*****, is_test: {is_test}')

    # Create Binance client
    binance_client = BinanceSwapClient(
        api_key=api_key,
        api_secret=api_secret,
        is_test=is_test,
    )

    # Parse symbol
    symbol = parse_symbol(symbol_str)

    try:
        # Query the order
        order = binance_client.query_order(order_id, symbol)

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
        logger.error(f"Failed to query order {order_id} for symbol {symbol_str}: {str(e)}")
        print(f"Error: {str(e)}")
        sys.exit(1)
