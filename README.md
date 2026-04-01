# Chaos MVP

`Chaos` 是一个以 32 人 AI 生存博弈为核心的叙事项目。产品目标不是传统命令菜单游戏，而是一个由世界时钟驱动、以聊天会话承载公开交流与私聊的多角色互动系统。

当前仓库已经改为 Web MVP：后端使用 Python 标准库提供 HTTP 服务，前端是单页聊天界面。它承接了第 1 轮《诱饵均值》的现有玩法，并按 PRD 先落地了公共大厅、广播窗口、私聊窗口、轮询式状态同步和轻量后台调度。

## 当前实现

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

## 目标架构

- 玩家是 `001` 号参赛者视角，不是系统特判对象
- 玩家与 NPC 共享统一 `speak(target_scope, text)` 动作模型
- 世界时间独立推进，玩家不输入时，其他角色也会继续行动
- 前端采用聊天软件式 UI，区分公共大厅、广播会话和私聊会话
- 前端只展示玩家可见的信息，不暴露全部底层消息流
- 广播与环境文本都进入统一消息系统，但环境文本不作为独立 agent

更完整的产品与技术整合说明见 [docs/PRD.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/PRD.md)。

## 运行

```bash
python3 main.py
```

默认启动后访问 `http://127.0.0.1:8000`。

当前版本为 LLM 强依赖模式：不会使用离线回退文本或规则化行为。
如果模型配置错误或调用失败，接口会直接返回错误信息用于调试。

运行前请准备可用的 `key.yaml`（火山引擎）：

```bash
python3 main.py
```

模型配置读取自 `key.yaml`：

- `model`：填写火山引擎 endpoint ID，即 `ep-...`
- `model_name`：仅用于显示
- `apikey`：火山引擎 API Key

## 文档

- [docs/PRD.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/PRD.md)：产品总纲，已并入聊天 UI、世界时钟、消息模型和实施路线
- [docs/Agent设计.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/Agent设计.md)：agent 输入输出、动作接口和调度原则
- [docs/对话与记忆系统设计.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/对话与记忆系统设计.md)：消息写入、记忆压缩、可见输入
- [docs/回合结构设计.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/回合结构设计.md)：阶段状态切换与 `available_actions`
- [docs/每轮游戏专属API说明.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/每轮游戏专属API说明.md)：五轮游戏专属动作与校验
- [docs/角色池设定.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/角色池设定.md)：角色池与主角模板
- [docs/README.md](/mnt/c/Users/Leslie_Jiang/Desktop/project/chaos/docs/README.md)：文档目录与分工

## 后续重点

- 将当前标准库 Web 服务替换为 `FastAPI`
- 将静态单页前端升级为 `React + Vite`
- 把世界时钟从轻量后台调度扩展为更完整的自主行动系统
- 补齐广播、环境文本、记忆摘要与状态存档的可视化
- 在聊天版里继续扩展第 2 至第 5 轮
