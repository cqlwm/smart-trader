# 修改 GeneralStrategy 以限制 K 线最大行数的计划

## 目标

对 `GeneralStrategy` 中存储的 K 线 DataFrame 进行行数限制，防止长时间运行后内存占用过大及操作变慢。当行数达到指定的最大值时，自动丢弃较旧的一半 K 线数据。

## 修改步骤

### 1. 修改 `strategy/__init__.py` 中的 `GeneralStrategy`

* **初始化方法 (`__init__`)**:

  * 新增实例变量 `self.max_kline_nums = 2000`，作为默认的最大K线保留行数（用户可以在子类中根据需要修改此值）。

* **更新K线方法 (`_update_klines`)**:

  * 在处理完K线的更新（修改最后一行）或追加（拼接新行）逻辑之后，获取当前最新的 DataFrame。

  * 判断如果 `len(df) >= self.max_kline_nums`，则计算需要保留的行数（如 `keep_nums = self.max_kline_nums // 2`）。

  * 使用切片操作 `df = df.iloc[-keep_nums:].reset_index(drop=True)` 保留最新的这部分数据。

  * 重新将 `df` 赋值给 `self.kline_data_dict[symbol][timeframe].klines`。

### 2. 编写测试验证修改

* 创建 `test/test_general_strategy.py` 文件（如果尚未有相关测试）。

* 编写测试用例：

  * 初始化一个测试用的 `GeneralStrategy`，设定 `max_kline_nums = 10`。

  * 模拟传入多条 `Kline` 数据，使得累积行数达到 `10` 行。

  * 验证达到 `10` 行后，行数是否被裁剪到了 `5` 行，且保留的是最后 5 条数据。

  * 确保追加新行和更新当前行的逻辑在裁剪后仍能正常工作。

## 预期效果

* 当策略长时间运行时，K 线数据将被控制在可预期的内存占用范围内，防止内存泄漏和因 DataFrame 过大导致的数据处理性能下降。

* `reset_index(drop=True)` 保证了裁剪后的 DataFrame 索引依旧是连续的，不会影响后续的其他操作。

