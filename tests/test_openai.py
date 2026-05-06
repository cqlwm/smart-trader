from openai import OpenAI

client = OpenAI(
    api_key="sk-zhgjbwheyfxpfblwuoscoxrdngvpntgtgsejprcrkmkosbld",
    base_url="https://api.siliconflow.cn/v1"
)

response = client.chat.completions.create(
    model="deepseek-ai/DeepSeek-V4-Flash",
    messages=[
        {"role": "system", "content": "你是一个有用的助手"},
        {"role": "user", "content": "你好，请介绍一下你自己"}
    ],
    reasoning_effort="xhigh",
    extra_body={
        "thinking": {"type": "enabled"},
        # "enable_thinking": True
    }
)
# print(response.choices[0].message.reasoning_content)

print(response.choices[0].message.content)