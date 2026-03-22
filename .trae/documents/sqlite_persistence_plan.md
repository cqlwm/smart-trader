# SQLite 持久化改造设计方案

## 一、 架构设计目标

当前 `strategy` 模块中的状态持久化直接依赖于 JSON 文件（如 `OrderRecorder` 和 `SimpleGridStrategy` 的 `save_state`/`load_state`）。这种方式存在耦合度高、并发读写存在隐患、且不利于后续复杂查询（如历史订单统计、多策略状态汇总）等问题。

为保持**解耦**和**可扩展性**，本方案采用 **Repository Pattern（仓储模式）** 和 **Dependency Injection（依赖注入）** 进行架构设计。

## 二、 核心架构设计

### 1. 抽象接口层 (Repository Interface)

定义统一的持久化接口，将策略的业务逻辑与底层的存储介质完全隔离。遵循**开闭原则**，未来若需切换到 MySQL/PostgreSQL，只需新增实现类而无需修改策略代码。

```python
from abc import ABC, abstractmethod
from typing import TypeVar, Generic

T = TypeVar('T')

class StrategyRepository(ABC, Generic[T]):
    @abstractmethod
    def save_state(self, strategy_id: str, state: T) -> None:
        """保存策略状态"""
        pass

    @abstractmethod
    def load_state(self, strategy_id: str) -> T | None:
        """加载策略状态"""
        pass
```

### 2. 存储实现层 (SQLite Implementation)

考虑到 `CLAUDE.md` 要求的“简化逻辑”和“减少非必要的抽象”，我们优先使用 Python 内置的 `sqlite3` 模块，无需额外安装 ORM 库，保持轻量级。

针对可扩展性，SQLite 的表结构设计提供两种演进路径（推荐路径 B）：

* **路径 A（KV 存储过渡版）**：直接将 Pydantic 模型 `model_dump_json()` 后的字符串存入 SQLite 的 `TEXT` 字段。优点是迁移成本极低。

* **路径 B（关系型存储标准版，推荐）**：设计规范的表结构，如 `strategy_instance`（策略实例）、`grid_orders`（网格订单）、`trade_history`（交易历史）。这为后续的利润统计、前端 Dashboard 数据展示提供 SQL 查询支持。

### 3. 依赖注入 (Dependency Injection)

修改 `SimpleGridStrategy` 和 `SignalGridStrategy`（及 `OrderManager`），通过构造函数注入 `StrategyRepository` 实例。

```python
class SimpleGridStrategy:
    def __init__(
        self, 
        symbol: str, 
        # ... 其他参数
        repository: StrategyRepository | None = None
    ) -> None:
        # 默认使用 SQLite 实现，也可以在外部组装时传入测试用的 MockRepository
        self._repository = repository or SQLiteGridRepository(db_path="data/trading.db")
        self.strategy_id = f"simple_grid_{symbol}"
        self.load_state()
```

## 三、 模块与目录规划

建议在项目根目录或核心目录下新增 `persistence` 模块：

```text
smart-trader/
├── persistence/
│   ├── __init__.py
│   ├── base.py              # 定义 StrategyRepository 等抽象基类
│   ├── sqlite_repo.py       # 包含 SQLite 具体的 CRUD 实现
│   └── exceptions.py        # 定义自定义异常，如 DatabaseConnectionError
├── strategy/
│   ├── simple_grid_strategy.py  # 移除 JSON 操作，改为调用 self._repository
│   ├── signal_grid_strategy.py
│   └── order_manager.py     # 改造 OrderRecorder，使其依赖 repository
```

## 四、 具体实施步骤 (Todo List 规划)

1. **基础环境准备与接口定义**

   * 创建 `persistence/base.py`，定义 `StrategyRepository` 抽象接口。

   * 创建 `persistence/exceptions.py`，定义 `PersistenceError` 等业务异常（遵循精准捕获原则）。

2. **开发 SQLite 仓储类**

   * 在 `persistence/sqlite_repo.py` 中实现 `SQLiteGridRepository`。

   * 使用 `sqlite3` 建立数据库连接池或上下文管理器，确保线程安全（替代现有的 `threading.RLock` 文件锁逻辑）。

   * 编写对应的 `CREATE TABLE` 初始化语句。

   * **TDD 要求**：编写 `tests/test_sqlite_repo.py`，使用 `:memory:` 内存数据库进行增删改查单元测试。

3. **策略类改造 (Refactoring)**

   * 改造 `SimpleGridStrategy`：移除 `save_state` 和 `load_state` 中的 `with open(...)` JSON 逻辑，替换为 repository 调用。

   * 改造 `SignalGridStrategy` & `OrderManager`：将 `OrderRecorder` 的职责弱化为纯数据模型（Pydantic），持久化动作交由 `OrderManager` 调用注入的 repository 完成。

4. **数据迁移 (Data Migration)**

   * 编写一个一次性脚本 `scripts/migrate_json_to_sqlite.py`，读取现有的 `data/*.json` 并通过 repository 写入 SQLite，确保现有运行策略的状态不丢失。

5. **系统集成与测试**

   * 运行全局测试用例。

   * 观察日志输出（确保符合日志规范，记录 SQLite 的关键动作与异常）。

## 五、 符合规范的细节考量

* **异常处理**：捕获 `sqlite3.Error`，记录 ERROR 级别日志，并抛出自定义的 `PersistenceError`，避免策略层直接感知到底层数据库异常。

* **类型注解**：所有的返回值、参数必须严格添加类型注解（如 `dict[str, Any]`、`T | None`）。

* **线程安全**：SQLite 默认支持多线程读取，但并发写入可能会遇到 `database is locked`。需要在连接时增加 `timeout` 参数，并在必要时使用队列或文件锁机制，或者开启 SQLite 的 WAL（Write-Ahead Logging）模式。

