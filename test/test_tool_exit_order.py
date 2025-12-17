import json
import sys

# 从启动参数中获取file路径
file_path = sys.argv[1]

# 读取JSON文件内容
with open(file_path, 'r') as file:
    data = json.load(file)

