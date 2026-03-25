# Chaos MVP

当前实现是终端版 MVP，只跑通第 1 轮《诱饵均值》。

## 运行

```bash
python3 main.py
```

模型配置读取自 `key.yaml`：

- `model`：填写火山引擎 endpoint ID，即 `ep-...`
- `model_name`：仅用于显示
- `apikey`：火山引擎 API Key

## 当前范围

- 载入 32 名角色数据
- 固定玩家开场设定
- 读取 `key.yaml` 并接入火山引擎 Responses API
- 使用 Responses API 缓存共享前缀提示词
- 环境引导与帮助提示
- 广播宣读规则前的自由社交
- 广播宣读规则后的第 1 轮执行
- 玩家通过 `speak` 与环境或角色互动
- 玩家秘密提交数字
- NPC 优先通过 LLM 生成发言、回复与数字
- 结算平均值、目标数、存活名单

## 后续扩展

- 真正的多阶段记忆输入
- 更完整的对话和环境查询
- 第 2 至第 5 轮
