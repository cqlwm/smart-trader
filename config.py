import json

with open('config.json', 'r') as f:
    config = json.load(f)

# 项目根目录
PROJECT_PATH = config['project_path']

# 数据目录
DATA_PATH = f'{PROJECT_PATH}/data'

print(PROJECT_PATH)
print(DATA_PATH)
