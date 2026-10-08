# 编码设计

本文件沉淀跨模块的编码设计决策与约束（AGENTS.md 第三节要求）。
修改代码前先查阅本文档；设计变更时先更新本文档，再动代码。

## 一、总则

1. **记录模型用 Pydantic**：DB 行 → Record → Domain → Dict 的既有转换模式（见 AGENTS.md 第五节），
   禁止用 `dict[str, Any]` 承载业务语义；`extra="forbid"` 保持开启，字段拼写错误立即报错。
2. **易于测试**：外部依赖必须有内存实现（`tests/fakes.py`）；测试不触网、不落盘、相互独立。
3. **可扩展**：一切"复制粘贴的第二份真相"都是缺陷——同一信息只允许有一个权威来源，
   其余位置从权威来源派生或引用。
4. **可读性**：模块按职责拆分，单文件不允许混装"数据契约 + 表结构 + 查询逻辑"。

## 二、存储层设计（storage）

### 1. 模块职责

| 文件 | 职责 | 依赖 |
| --- | --- | --- |
| `storage/records.py` | 记录模型（Pydantic）：`GridPositionRow` / `OrderRecord` / `IdRecord` / `OrderIntent` | `model` |
| `storage/schema.py` | 表结构元数据（SQLAlchemy Core `Table`）：列/约束/索引 | `sqlalchemy` |
| `storage/order_journal.py` | `OrderJournal` 仓储：语句构造 + 事务 + 连接管理 | `records` / `schema` |

**SQLAlchemy 只允许出现在 storage 内部**（`schema.py` / `order_journal.py`），
对 execution / strategy / 测试暴露的永远是 Pydantic 记录与 `OrderJournal`。

### 2. schema 单一来源

- 列定义只在 `schema.py` 出现一次；建库走 `metadata.create_all()`（替代硬编码 DDL 字符串）。
- 列类型刻意与历史 DDL 对齐（`TEXT` / `INTEGER` / `REAL`），保证新旧库完全兼容。
- 有意调整库结构时必须：改 `schema.py` → 更新 `tests/test_storage_schema.py` 的冻结快照 →
  评估旧库迁移；任何**无意**漂移都会被等价性测试抓住。
- 唯一允许保留裸 SQL 的地方是 `_migrate_legacy_schema`：它面对的是未知的历史结构，
  裸 SQL（`ALTER TABLE ... RENAME` / `REPLACE`）才是正确工具；其余语句一律使用 Core 构造。

### 3. 语句构造（为什么是 Core 而不是 declarative ORM / SQLModel）

- 三张表**零外键、零关系、零对象图**，declarative ORM 的 relationship / unit-of-work / identity map
  没有用武之地；引入它只会带来第二套模型体系（双映射）或挤掉 Pydantic 的记录语义。
- SQLModel 的"单类双用"看似省事，但 pyright strict 兼容性与 0.0.x 稳定性不满足本项目
  （0 errors / 0 warnings）的门禁要求。
- Core 恰好覆盖痛点：schema 单源、参数化 UPSERT、映射自动化。
- 若未来真需要 declarative ORM，现有 `Table` 可被 imperative mapping 直接复用，不是单行道。

### 4. 泛型 upsert

- `_upsert(conn, table, primary_key, record)`：列值来自 `record.model_dump(mode="json")`；
- 冲突时更新**除主键与 `created_at` 外**的全部列——`created_at` 首写不变，与旧实现语义一致；
- `on_conflict_do_update` 的 `set_` 是 dict，杜绝"位置参数错一位 = 静默串数据"。

### 5. 连接与事务模型

- 每个方法用 `engine.begin()` / `engine.connect()` 独占一次连接与事务，**替代**旧的
  共享连接 + `threading.RLock`；方法内部不再需要锁。
- 文件库：连接时设置 `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=30000`，
  并发写由 SQLite 自行串行化（多策略共享一个库的主要并发路径是"写-写"）。
- 内存库（测试）：`StaticPool` + `check_same_thread=False`，单连接共享。
- 原子性不变量不变：`confirm_entry` / `confirm_exit` 的"仓位 + 流水 + 核销"必须同一事务
  （见 `01-system-architecture.md` 第十一节）。

### 6. 映射（Row → Record）

- 常规表：`GridPositionRow.model_validate(dict(row))`——列名与字段名 1:1，
  枚举 / 布尔由 Pydantic 宽松模式从 DB 原始值恢复（`"buy"` → `OrderSide.BUY`，`0/1` → `bool`）。
- 例外：`order_ids.intent` 是 JSON 文本列，显式走 `OrderIntent.model_validate_json()`。
- 映射代码不引入独立适配层；若某列未来需要特殊转换，在该列的读取处就近处理并补测试。

### 7. 已知权衡（记录在案）

- `:memory:` + StaticPool 单连接在**多线程并发**下共享同一 sqlite 连接；
  当前测试为单线程，行为无影响；生产走文件库（多连接 + WAL）。若未来出现内存库多线程测试，
  需要先升级为"每线程独立库"或改用文件库。
- `rowid` 排序（`load_positions` / `load_archived_positions` / `load_order_records` 的稳定 tie-break）
  通过 `text("rowid")` 保留，不进入 metadata（SQLite 隐式列，无需定义）。
