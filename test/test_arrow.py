# 安装 PyArrow
# pip install pyarrow

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# 1. pandas DataFrame 转 Arrow 表
df = pd.DataFrame({
    'id': [1, 2, 3],
    'name': ['Alice', 'Bob', 'Charlie'],
    'age': [25, 30, 35]
})

# 转换为 Arrow 表
arrow_table = pa.Table.from_pandas(df)

# 2. Arrow 表转 pandas DataFrame
new_df = arrow_table.to_pandas()

# 3. 使用 Arrow 格式进行高效I/O操作
# 写入 Parquet 文件（使用 Arrow 引擎）
df.to_parquet('data.parquet', engine='pyarrow')

# 读取 Parquet 文件
df_read = pd.read_parquet('data.parquet', engine='pyarrow')

# 4. 对于需要频繁追加数据的场景，可以考虑使用 Arrow 作为底层存储
# 使用 Arrow 的 ChunkedArray 结构支持高效追加
# 然后在适当的时候转换为 DataFrame 进行分析