# SMC Signal Redesign Design

## Overview

Redesign the SMC signal generation layer. Core SMC detection logic (choch, bos, OB, FVG) remains unchanged. The new signal layer implements: K-line close → detect choch/bos structure break → find nearest OB in current structure → place limit order at OB mid price.

## Architecture

Pure functional signal computation + strategy class integration. Signal logic lives in pure functions for testability and backtest compatibility; the strategy class wraps these functions and handles IO (order placement/cancellation).

## Data Structures

```python
class SignalStatus(Enum):
    PENDING = auto()      # Limit order waiting to fill
    FILLED = auto()       # Order filled
    CANCELED = auto()     # OB mitigated, order canceled
    EXPIRED = auto()      # Other expiration reasons

class Signal(BaseModel):
    id: str
    ob: OrderBlock            # The OB that triggered this signal
    event: StructureEvent     # The choch/bos event
    direction: Bias           # BULLISH=long, BEARISH=short
    entry_price: float        # OB mid price
    stop_loss: float          # OB edge + ATR buffer
    take_profit: float | None # Opposite OB (if RR>2), else None
    created_bar_index: int    # Bar index when signal created
    status: SignalStatus

class SMCSignalConfig(BaseModel):
    symbol: str
    timeframe: str
    atr_multiplier: float = 0.5
    min_rr: float = 2.0

class SignalState(BaseModel):
    signals: list[Signal]         # All signals (including history)
    last_swing_event_index: int   # Last processed swing event bar_index
    last_internal_event_index: int # Last processed internal event bar_index
```

## Signal Generation Flow

```
K-line close → SMC engine → SMCResult
                    ↓
      compute_signals(smc_result, prev_state, bar_index)
                    ↓
   1. Detect new structure events:
      - Compare SMCResult.swing_state.last_event.bar_index with prev_state.last_swing_event_index
      - Compare SMCResult.internal_state.last_event.bar_index with prev_state.last_internal_event_index
      - If bar_index > prev index → new event detected
   2. For each new event, find nearest unmitigated OB:
      - Swing event → search swing_order_blocks only
      - Internal event → search internal_order_blocks only
   3. Calculate entry (OB.mid), stop_loss (OB edge + ATR), take_profit (opposite OB from same pool)
   4. Check RR ratio > 2; if not, take_profit = None
   5. Create Signal, append to state
   6. Check all PENDING signals: if their OB.status == MITIGATED → CANCELED
   7. Check price vs entry: if current bar's high/low reaches entry → FILLED
   8. Return new SignalState
```

## Signal Direction & Price Calculation

**Direction**: Determined by the structure event's `bias` field.
- BULLISH event → long (buy limit order)
- BEARISH event → short (sell limit order)

**Entry price**: OB mid price (`ob.mid`)

**Stop loss**:
- Long: `ob.low - atr * atr_multiplier`
- Short: `ob.high + atr * atr_multiplier`

**Take profit**:
- Long: nearest bearish OB's `low` (must satisfy RR > 2)
- Short: nearest bullish OB's `high` (must satisfy RR > 2)
- If no opposite OB satisfies RR > 2: `take_profit = None`

**RR ratio**: `|take_profit - entry| / |entry - stop_loss|` must be > 2.0

## OB Selection Rules

When a choch/bos event fires:
1. **Pool selection** (match event level to OB level):
   - Swing event → `SMCResult.swing_order_blocks`
   - Internal event → `SMCResult.internal_order_blocks`
2. Filter: `status != MITIGATED` AND `bias` matches event direction
3. Sort: by `formed_index` descending (most recent first)
4. Pick: the top 1 (most recently formed, unmitigated, same-direction OB)

**Take profit OB**: searched from the same pool (swing/internal) as the entry OB, opposite direction.

## Signal Lifecycle

- **PENDING → FILLED**: In `compute_signals`, check if the current bar's price action (high for long, low for short) reached the entry_price
- **PENDING → CANCELED**: The signal's OB becomes mitigated (checked via `OB.status == MITIGATED` in SMCResult)
- Signals persist as long as their OB remains unmitigated
- New signals are appended; existing unmitigated signals are never replaced
- Filled signals are tracked for P&L calculation

## File Structure

```
strategies/smc/
├── core/                    # Existing, unchanged
│   ├── structure.py
│   ├── order_blocks.py
│   ├── fvg.py
│   └── ...
├── models/
│   └── types.py             # Existing, add Signal/SignalState/SignalStatus
├── engine.py                # Existing, unchanged
├── signal.py                # NEW: pure function signal computation
└── smc_strategy.py          # NEW: SimpleStrategy subclass
```

No new backtest-specific files. The strategy integrates with the existing backtest framework via `strategies.yaml` configuration.

## Strategy Class

```python
@register_strategy("smc_signal")
class SMCSignalStrategy(SimpleStrategy):

    config: SMCSignalConfig

    def __init__(self, ...):
        self._signal_state = SignalState(signals=[], last_event_index=-1)

    def _on_kline_finished(self):
        """Called on each K-line close - main signal logic entry point"""
        # 1. Get updated kline data
        # 2. Run SMC engine analysis
        # 3. compute_signals(smc_result, self._signal_state, bar_index)
        # 4. For new PENDING signals → submit limit orders via client
        # 5. For CANCELED signals → cancel corresponding orders via client
        # 6. Update internal _signal_state
```

**Why `_on_kline_finished`**: Framework guarantees this is called only when a K-line closes, matching the "detect on close" requirement. Consistent with existing strategies (SignalGridStrategy, etc.).

## Configuration (strategies.yaml)

```yaml
- name: smc_signal
  type: smc_signal
  config:
    symbol: BTCUSDT
    timeframe: 15m
    atr_multiplier: 0.5
    min_rr: 2.0
```

## Core Function Signatures

```python
def compute_signals(
    result: SMCResult,
    prev_state: SignalState,
    current_bar_index: int,
) -> SignalState:
    """Pure function: compute new signal state from SMC analysis result"""

def find_entry_ob(
    order_blocks: list[OrderBlock],
    event: StructureEvent,
) -> OrderBlock | None:
    """Find nearest same-direction unmitigated OB"""

def find_take_profit_ob(
    order_blocks: list[OrderBlock],
    direction: Bias,
    entry: float,
    stop_loss: float,
    min_rr: float = 2.0,
) -> OrderBlock | None:
    """Find opposite-direction OB as TP target, must satisfy RR ratio"""

def calculate_stop_loss(
    ob: OrderBlock,
    direction: Bias,
    atr: float,
    atr_multiplier: float = 0.5,
) -> float:
    """Calculate stop loss: OB edge + ATR buffer"""
```

## Testing Strategy

1. Unit tests for each pure function (`find_entry_ob`, `find_take_profit_ob`, `calculate_stop_loss`, `compute_signals`)
2. Integration test: feed historical kline sequence, verify signal generation matches expected behavior
3. Backtest via existing framework: configure in `strategies.yaml`, run through backtest runner

## Constraints

- choch and bos have equal priority — both trigger signal generation
- No additional signal filters (no FVG confirmation, no volume confirmation, no multi-timeframe confirmation)
- One signal per event: only the nearest OB is used
- Limit orders remain active until OB is mitigated
