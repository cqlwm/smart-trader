import json

# 读取JSON文件内容
with open('/Users/li/Downloads/backup_dogeusdc_short_sell.json', 'r') as file:
    data = json.load(file)

# 初始化总收益
total_profit = 0.0

# 遍历所有交易项，累加收益
for item in data['items']:
    total_profit += item['total_profit']

# 输出总收益（保留6位小数，适应加密货币收益精度）
print(f"总收益为: {total_profit:.6f} USDC")