# V1：双均线（1h/4h EMA200）同向交易版（不加仓）。
# 恢复自 git commit 326a50a（分层重构前），供独立选用；与 V2（分层加仓版）分别维护。
# 2026-08-08 重构：保持默认行为不变，新增 use_alpha_take_profit 开关，拆分小函数提升可读性。
"""Alpha Trend 趋势跟踪指标（V1：双均线同向交易版）。

基于 ATR 通道和 MFI 资金流向的波动率自适应趋势跟踪算法。
将原始 OHLCV DataFrame 转为带 alpha_trend 曲线、累积计数信号与
双均线（1h/4h EMA200）同向进出场信号的完整分析表。

算法原理
--------
1. 计算 ATR(period) 通道：
   - atr_base_low  = low  - ATR × atr_multiple  （下轨）
   - atr_base_high = high + ATR × atr_multiple  （上轨）

2. 以 MFI(period) ≥ 50 判断牛熊方向：
   - MFI ≥ 50 → 看涨，trend 线绑定下轨
   - MFI < 50 → 看跌，trend 线绑定上轨

3. 棘轮迭代（alpha_trend 线）：
   - 看涨阶段：alpha_trend[i] = max(alpha_trend[i-1], atr_base_low[i])  （只向上，锁低点）
   - 看跌阶段：alpha_trend[i] = min(alpha_trend[i-1], atr_base_high[i]) （只向下，锁高点）

4. 信号生成（累积计数）：
   - alpha_trend[i] 较 2 根前更高 → 买入计数递增；更低 → 卖出计数递减
   - 方向切换时重置为 ±1；NaN 区域填 0；无信号时 carry forward 当前计数值

5. 突破标记（is_breakout）：方向切换或首根建立方向的 K 线标记为 True。

输出列
------
向输入的 DataFrame 添加以下列：

- alpha_value    : 棘轮趋势线
- trend          : 累积计数信号（>0 看多、<0 看空、0 为 NaN/无方向）
- is_breakout    : 趋势突破根标记（bool）
- ema200         : 同周期 EMA(200)，前 200 根为 NaN（1h 均线）
- htf_ema200     : 高时间级别 EMA(200)，按收盘时间 backward 对齐到 LTF（仅传 htf_df 时输出，4h 均线）
- long_entry     : 多头进场 = 无多头持仓 AND 看涨突破 AND close>1h ema200 AND close>4h ema200
- short_entry    : 空头进场 = 无空头持仓 AND 看跌突破 AND close<1h ema200 AND close<4h ema200
- long_exit      : 多头离场 = 持仓期内（浮盈：alpha 止盈 OR 看跌突破；浮亏：前段极值硬止损）
- short_exit     : 空头离场 = 持仓期内（浮盈：alpha 止盈 OR 看涨突破；浮亏：前段极值硬止损）

V1 采用 V2 风格持仓状态机（单仓模型，不加仓）：
- 进场：该方向无持仓时，突破根 + 1h/4h EMA200 同向门禁 -> 开仓，记录 entry_price 与 hard_stop。
- 离场（有持仓才可能）：
  - 浮盈（close > 开仓价，多头）：alpha 反向穿越止盈（use_alpha_take_profit 时）或 反向突破 -> 全平
  - 浮亏（close <= 开仓价）：只有硬止损（close 跌破开仓前最后一段空头趋势段最低 low；
    空头对称：close 升破开仓前最后一段多头趋势段最高 high）-> 全平
  - ema200 只做方向确认（进场门禁），不再触发离场（V2 风格）。
- 平仓后状态复位；同方向持仓期间不重复进场。

前段极值硬止损（结构性 swing low/high，与 V2 分层版口径一致）：
- 开仓时记录开仓前最后一个连续 trend 段的极值：多头=前空头段内 low 最低点，空头=前多头段内 high 最高点。
- 持仓期内 close 反向突破该极值即全平（事件语义，只在触发那根为 True），平仓后复位。
- 无前段（首根信号即突破）时该方向无硬止损位，不触发额外平仓。

说明：
- ATR / atr_base_low / atr_base_high / MFI 为内部计算量，不输出（需要 ATR 时用 TA-Lib 单独算）。
- exit 取「穿越事件」语义：只有下穿/上穿那根 K 线为 True，持续在错误侧不重复触发。
- 传 htf_df 时进场额外要求 close 在 1h 与 4h EMA200 同侧；某行 4h ema 为 NaN（warmup
  不足或无对齐 HTF）时该行回退到 LTF-only 条件。
- ema200 不足（前 200 根 / NaN 区域）时四列信号全 False。

alpha 反向穿越止盈（use_alpha_take_profit=True 时并入 exit 列，事件语义）：
- 多头段（trend>0）：close 下穿 alpha_value 时，按 alpha 平台去重记录 (alpha, low)；
  之后任一 K 线 close 跌破某个已记录 low，且当前持仓浮盈（close > 进场价）-> 触发多头止盈。
- 空头段（trend<0）：close 上穿 alpha_value 时，按 alpha 平台去重记录 (alpha, high)；
  之后任一 K 线 close 升破某个已记录 high，且当前持仓浮盈（close < 进场价）-> 触发空头止盈。
- 持仓浮亏时不触发（交给前段极值硬止损 / 反向突破等浮亏侧离场）；只在触发那根为 True；
  反向突破（新一轮趋势开始）或止盈触发后清空记录。
  进场价 = 该方向最近一次进场信号（long_entry / short_entry）的收盘价。
"""

import numpy as np
import pandas as pd
import talib as ta
from pandas import DataFrame

_high = "high"
_low = "low"
_close = "close"
_volume = "volume"
_datetime = "datetime"
_alpha_value = "alpha_value"
_trend = "trend"
_is_breakout = "is_breakout"
_ema200 = "ema200"
_htf_ema200 = "htf_ema200"
_long_entry = "long_entry"
_long_exit = "long_exit"
_short_entry = "short_entry"
_short_exit = "short_exit"
_ema_period = 200


def alpha_trend_indicator(
    df: DataFrame,
    atr_multiple: float = 1.0,
    period: int = 8,
    htf_df: DataFrame | None = None,
    use_alpha_take_profit: bool = True,
) -> DataFrame:
    """计算 Alpha Trend 指标，返回原 DataFrame 的副本并添加指标列。

    Parameters
    ----------
    df : DataFrame
        必须包含 `high, low, close, volume` 列。
        （可选 `datetime` 列，仅传递不做计算）
    atr_multiple : float, optional
        ATR 倍数，默认 1.0。越大通道越宽，趋势线越不敏感。
    period : int, optional
        ATR/MFI 计算周期，默认 8。越小对价格变化越敏感。
    htf_df : DataFrame, optional
        高时间级别 K 线（如 4h/1d），用于 4h EMA200 对齐。非空时：
        - 计算 HTF 的 EMA(200)，按 K 线收盘时间 backward 对齐到 LTF（无前视偏差），
          输出 `htf_ema200` 列。
        - 进场新增条件：close 必须同时在 1h 与 4h EMA200 同侧（多头 close>两者，
          空头 close<两者）。离场不变。
        - None 时回退到 LTF-only 行为，不输出 `htf_ema200` 列。
    use_alpha_take_profit : bool, optional
        是否把 alpha 反向穿越止盈并入 `long_exit` / `short_exit`，默认 True。
        False 时浮盈侧离场只剩「反向突破」（浮亏侧仍由硬止损离场）。

    Returns
    -------
    DataFrame
        原 df 的副本，新增 `alpha_value, trend, is_breakout` 列；以及
        `ema200, long_entry, long_exit, short_entry, short_exit`
        和（传 htf_df 时）`htf_ema200` 列。
    """
    df = df.copy()  # 只读入参: 指标列加在副本上, 不污染策略共享的 klines DataFrame
    high, low, close, volume = _extract_ohlcv(df)
    atr_base_low, atr_base_high, mfi = _compute_atr_channel(
        high, low, close, volume, period, atr_multiple
    )
    alpha = _compute_alpha_ratchet(atr_base_low, atr_base_high, mfi, period)
    trend, is_breakout = _compute_trend_counter(alpha)

    df[_alpha_value] = alpha
    df[_trend] = trend
    df[_is_breakout] = is_breakout

    _compute_entry_exit_columns(df, htf_df, use_alpha_take_profit)

    return df


def _extract_ohlcv(
    df: DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """提取 high/low/close/volume 四个 numpy 数组（float64）。"""
    return (
        df[_high].to_numpy(dtype=np.float64),
        df[_low].to_numpy(dtype=np.float64),
        df[_close].to_numpy(dtype=np.float64),
        df[_volume].to_numpy(dtype=np.float64),
    )


def _compute_atr_channel(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    period: int,
    atr_multiple: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算 ATR 通道（下轨/上轨）与 MFI。"""
    atr = ta.ATR(high, low, close, timeperiod=period)
    atr_range = atr * atr_multiple
    atr_base_low = low - atr_range
    atr_base_high = high + atr_range
    mfi = ta.MFI(high, low, close, volume, timeperiod=period)
    return atr_base_low, atr_base_high, mfi


def _compute_alpha_ratchet(
    atr_base_low: np.ndarray,
    atr_base_high: np.ndarray,
    mfi: np.ndarray,
    period: int,
) -> np.ndarray:
    """棘轮迭代：看涨阶段锁下轨低点、看跌阶段锁上轨高点。前 period 根为 NaN。"""
    n = len(atr_base_low)
    alpha = np.full(n, np.nan)
    if period < n:
        alpha[period] = (
            atr_base_low[period] if mfi[period] >= 50 else atr_base_high[period]
        )
        for i in range(period + 1, n):
            if mfi[i] >= 50:
                alpha[i] = max(alpha[i - 1], atr_base_low[i])
            else:
                alpha[i] = min(alpha[i - 1], atr_base_high[i])
    return alpha


def _compute_trend_counter(alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """累积计数信号（trend）与突破根标记（is_breakout）。

    - alpha 较 2 根前更高 → 买入计数 +1；更低 → 卖出计数 -1（体现趋势加速）
    - 方向切换时重置为 ±1，并标记该根为突破根
    - alpha 为 NaN（warmup 区域）时计数为 0
    """
    n = len(alpha)
    trend = np.zeros(n, dtype=np.int64)
    is_breakout = np.zeros(n, dtype=bool)
    direction = 0  # 0=无方向, 1=买入, -1=卖出
    counter = 0

    for i in range(n):
        if np.isnan(alpha[i]):
            trend[i] = 0
            continue

        prev_alpha = (
            alpha[i - 2] if i >= 2 else np.nan
        )  # shift(2)，i<2 时 NaN 比较恒 False
        if alpha[i] > prev_alpha:
            if direction == 1:
                counter += 1
            else:
                direction = 1
                counter = 1
                is_breakout[i] = True
        elif alpha[i] < prev_alpha:
            if direction == -1:
                counter -= 1
            else:
                direction = -1
                counter = -1
                is_breakout[i] = True

        trend[i] = counter

    return trend, is_breakout


def _align_htf_ema(ldf: DataFrame, hdf: DataFrame) -> pd.Series:
    """计算 HTF EMA(200) 并按收盘时间 backward 对齐到 LTF。

    取每根 LTF 之前（含当根）收盘时间最接近的 HTF K 线的 ema 值，无前视偏差。
    要求 LTF/HTF 都含 datetime 列（否则抛 ValueError）。
    返回与 ldf 等长的 Series，索引对齐 ldf.index，缺失处为 NaN。
    """
    htf_close = hdf[_close].to_numpy(dtype=np.float64)
    htf_ema = ta.EMA(htf_close, timeperiod=_ema_period)

    if _datetime in ldf.columns and _datetime in hdf.columns:
        # merge_asof 要求双方按 datetime 升序
        ltf_times = ldf[_datetime].to_numpy()
        order = np.argsort(ltf_times, kind="stable")
        htf_times = hdf[_datetime].to_numpy()
        htf_order = np.argsort(htf_times, kind="stable")

        ltf_view = pd.DataFrame({_datetime: ltf_times[order]})
        htf_view = pd.DataFrame(
            {
                _datetime: htf_times[htf_order],
                "_htf_ema": htf_ema[htf_order],
            }
        )
        merged = pd.merge_asof(ltf_view, htf_view, on=_datetime, direction="backward")

        # 还原到 LTF 原始顺序
        result = np.full(len(ldf), np.nan, dtype=np.float64)
        result[order] = merged["_htf_ema"].to_numpy(dtype=np.float64)
        aligned = pd.Series(result, index=ldf.index)
    else:
        raise ValueError("ldf 与 hdf 必须包含 datetime 列才能做 HTF EMA 对齐")

    return aligned


def _last_bear_low(trend: np.ndarray, low: np.ndarray, i: int) -> float:
    """开仓（idx=i）之前最后一个连续负 trend 段内 low 的最低值。

    趋势段按 trend 连续同号划分（-1,-2,... 为同一空头段；变号即切换段）。
    多头开仓在看涨突破根（trend 变 +1），因此 i-1 处一定是空头段末尾；
    回溯到变号处即得最近空头段，取该段 low 最低点作为硬止损位。
    无前空头段（首根信号即看涨）时返回 NaN。
    """
    j = i - 1
    if j < 0 or trend[j] >= 0:
        return float("nan")
    seg_min = float("inf")
    while j >= 0 and trend[j] < 0:
        seg_min = min(seg_min, low[j])
        j -= 1
    return seg_min


def _last_bull_high(trend: np.ndarray, high: np.ndarray, i: int) -> float:
    """开仓（idx=i）之前最后一个连续正 trend 段内 high 的最高值（空头硬止损位，对称）。"""
    j = i - 1
    if j < 0 or trend[j] <= 0:
        return float("nan")
    seg_max = float("-inf")
    while j >= 0 and trend[j] > 0:
        seg_max = max(seg_max, high[j])
        j -= 1
    return seg_max


def _direction_sides(ldf: DataFrame, hdf: DataFrame | None) -> tuple[pd.Series, pd.Series]:
    """进场方向门禁：多头要求 close>1h ema 且 close>4h ema，空头对称。

    HTF ema 在某行为 NaN（warmup 不足 / 无对齐 HTF）时，该行回退到 LTF-only 条件。
    """
    close = ldf[_close]
    ema = ldf[_ema200]
    if hdf is not None and len(hdf) > 0:
        htf_ema = _align_htf_ema(ldf, hdf)
        ldf[_htf_ema200] = htf_ema
        htf_valid = htf_ema.notna()
        long_side = (close > ema) & (~htf_valid | (close > htf_ema))
        short_side = (close < ema) & (~htf_valid | (close < htf_ema))
    else:
        long_side = close > ema
        short_side = close < ema
    return long_side, short_side


def _compute_entry_exit_state_machine(
    ldf: DataFrame,
    long_entry_event: pd.Series,
    short_entry_event: pd.Series,
    use_alpha_take_profit: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """V2 风格单仓状态机：逐行迭代产出 long/short entry/exit 四列。

    进场（该方向无持仓时）：
    - 多头：long_entry_event（看涨突破根 + 方向门禁）-> 开多，记录 entry_price、hard_stop
    - 空头：short_entry_event（看跌突破根 + 方向门禁）-> 开空，对称

    离场（有持仓才可能，每根检查）：
    - 多头：
      - 浮盈（close > entry_price）：alpha 止盈（use_alpha_take_profit 时）或 看跌突破 -> 全平
      - 浮亏（close <= entry_price）：只有硬止损（close < hard_stop，前空头段最低 low）-> 全平
    - 空头对称：浮盈 close < entry_price（alpha 止盈/看涨突破）；浮亏只有硬止损（close > hard_stop）
    - ema200 只做方向确认，不触发离场。

    平仓后状态复位（entry_price/hard_stop 清零），同方向可再次进场。
    """
    close = ldf[_close].to_numpy(dtype=np.float64)
    high = ldf[_high].to_numpy(dtype=np.float64)
    low = ldf[_low].to_numpy(dtype=np.float64)
    alpha = ldf[_alpha_value].to_numpy(dtype=np.float64)
    trend = ldf[_trend].to_numpy(dtype=np.int64)
    is_breakout = ldf[_is_breakout].to_numpy(dtype=np.bool_)
    long_ev = long_entry_event.fillna(False).to_numpy(dtype=np.bool_)
    short_ev = short_entry_event.fillna(False).to_numpy(dtype=np.bool_)

    n = len(ldf)
    long_entry = np.zeros(n, dtype=np.bool_)
    short_entry = np.zeros(n, dtype=np.bool_)
    long_exit = np.zeros(n, dtype=np.bool_)
    short_exit = np.zeros(n, dtype=np.bool_)

    long_open = False
    long_entry_price = 0.0
    long_hard_stop = float("nan")
    long_base_price: list[tuple[float, float]] = []  # (alpha, low) 止盈参考
    short_open = False
    short_entry_price = 0.0
    short_hard_stop = float("nan")
    short_base_price: list[tuple[float, float]] = []  # (alpha, high) 止盈参考

    for i in range(n):
        c = close[i]
        t = int(trend[i])
        a = alpha[i]
        a_prev = alpha[i - 1] if i > 0 else float("nan")
        c_prev = close[i - 1] if i > 0 else float("nan")
        is_brk = bool(is_breakout[i])

        # close 反向穿越 alpha（记录 base_price，按 alpha 平台去重）
        alpha_cross_down = (
            not np.isnan(a) and not np.isnan(a_prev)
            and c < a and c_prev >= a_prev
        )
        alpha_cross_up = (
            not np.isnan(a) and not np.isnan(a_prev)
            and c > a and c_prev <= a_prev
        )
        if alpha_cross_down and (not long_base_price or long_base_price[-1][0] != a):
            long_base_price.append((a, low[i]))
        if alpha_cross_up and (not short_base_price or short_base_price[-1][0] != a):
            short_base_price.append((a, high[i]))
        if is_brk and t > 0:
            long_base_price.clear()
        if is_brk and t < 0:
            short_base_price.clear()

        # ---- 多头 ----
        if long_open:
            if c > long_entry_price:
                # 浮盈：alpha 止盈（use_alpha_take_profit 时）或 反向突破
                alpha_tp_hit = use_alpha_take_profit and any(
                    c < lp for _, lp in long_base_price
                )
                if alpha_tp_hit or (is_brk and t < 0):
                    long_exit[i] = True
                    long_open = False
                    long_entry_price = 0.0
                    long_hard_stop = float("nan")
                    long_base_price.clear()
            elif not np.isnan(long_hard_stop) and c < long_hard_stop:
                # 浮亏：只有硬止损（前空头段最低 low）
                long_exit[i] = True
                long_open = False
                long_entry_price = 0.0
                long_hard_stop = float("nan")
                long_base_price.clear()
        elif long_ev[i]:
            long_entry[i] = True
            long_open = True
            long_entry_price = c
            long_hard_stop = _last_bear_low(trend, low, i)
            long_base_price.clear()

        # ---- 空头 ----
        if short_open:
            if c < short_entry_price:
                # 浮盈：alpha 止盈（use_alpha_take_profit 时）或 反向突破
                alpha_tp_hit = use_alpha_take_profit and any(
                    c > hp for _, hp in short_base_price
                )
                if alpha_tp_hit or (is_brk and t > 0):
                    short_exit[i] = True
                    short_open = False
                    short_entry_price = 0.0
                    short_hard_stop = float("nan")
                    short_base_price.clear()
            elif not np.isnan(short_hard_stop) and c > short_hard_stop:
                # 浮亏：只有硬止损（前多头段最高 high）
                short_exit[i] = True
                short_open = False
                short_entry_price = 0.0
                short_hard_stop = float("nan")
                short_base_price.clear()
        elif short_ev[i]:
            short_entry[i] = True
            short_open = True
            short_entry_price = c
            short_hard_stop = _last_bull_high(trend, high, i)
            short_base_price.clear()

    return long_entry, short_entry, long_exit, short_exit


def _compute_entry_exit_columns(
    ldf: DataFrame,
    hdf: DataFrame | None = None,
    use_alpha_take_profit: bool = True,
) -> None:
    """计算 ema200 及 long/short entry/exit 四列，就地写入 df。

    采用 V2 风格持仓状态机（单仓模型）：
    - ema200 用输入 df 自身周期计算（1h 均线）；4h ema200 由 hdf 对齐。
    - 进场：该方向无持仓时，突破根 + 1h/4h EMA200 方向门禁 -> 开仓。
    - 离场（有持仓才可能）：
      - 浮盈：alpha 反向穿越止盈（use_alpha_take_profit 时）或 反向突破 -> 全平
      - 浮亏：只有硬止损（close 跌破前空头段最低 low / 升破前多头段最高 high）-> 全平
      ema200 只做方向确认，不再触发离场（V2 风格）。
    - 平仓后状态复位；同方向持仓期间不重复进场。
    """
    close = ldf[_close]

    ldf[_ema200] = ta.EMA(close.to_numpy(dtype=np.float64), timeperiod=_ema_period)
    ema = ldf[_ema200]

    is_breakout_col = ldf[_is_breakout]
    trend = ldf[_trend]

    # ---- 突破事件 ----
    bullish_breakout = is_breakout_col & (trend > 0)
    bearish_breakout = is_breakout_col & (trend < 0)

    # ---- 进场方向门禁：close 须在 1h 与 4h EMA200 同侧 ----
    long_side, short_side = _direction_sides(ldf, hdf)
    ema_valid = ema.notna()

    # ---- 进场信号（独立事件，状态机内部再按持仓过滤）----
    long_entry_event = (ema_valid & bullish_breakout & long_side).fillna(False)
    short_entry_event = (ema_valid & bearish_breakout & short_side).fillna(False)

    # ---- 状态机：单仓模型，逐行迭代 ----
    long_entry, short_entry, long_exit, short_exit = _compute_entry_exit_state_machine(
        ldf, long_entry_event, short_entry_event, use_alpha_take_profit,
    )
    ldf[_long_entry] = long_entry
    ldf[_short_entry] = short_entry
    ldf[_long_exit] = long_exit
    ldf[_short_exit] = short_exit
