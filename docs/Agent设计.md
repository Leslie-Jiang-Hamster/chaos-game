# Agent设计

## 1. 范围

MVP 包含 3 类 agent：

1. 选手 agent
2. 广播 agent
3. 环境 agent

玩家不是 agent。游戏引擎也不是 agent。

## 2. 总原则

- agent 的脑内可以自由推理
- agent 对世界的显式行为必须通过接口执行
- 抽象语义如“合作”“背叛”“威胁”不是动作接口
- 非游戏阶段基础动作只有 `speak(...)` 和 `end_phase()`
- 记忆不是显式接口，agent 不调用“读取记忆”动作，而是在决策时自然使用自己的记忆
- 所有存活选手一视同仁，不区分主要角色、次要角色、前台角色、后台角色
- 只要角色还活着，就按同一标准参与决策、记忆更新和阶段推进

## 3. 状态模型

### 全局状态

```text
round_number
phase
time_limit
time_left
alive_contestants
current_rule
available_actions
pending_events
```

### 私有记忆

```text
memory_items
memory_inferences
memory_biases
conversation_window
```

说明：
- “关系”不是独立硬状态，不单独维护 `trust`、`fear` 之类的显式字段
- 对某人是否可信、是否有好感、是否欠情、是否害怕，统一作为记忆中的判断、倾向和事件残留存在
- agent 在决策时直接使用自己的记忆，不暴露额外的记忆调用接口

## 4. 动作接口

### 基础动作

```text
speak(target_scope, text)
end_phase()
```

说明：
- `speak(...)` 是唯一基础显式表达动作
- `end_phase()` 表示该角色本阶段结束
- “结盟”“承诺”“求助”“指控”“威胁”都是 `speak(...)` 的语义结果

### `speak(...)`

```text
speak(target_scope, text)
```

```text
target_scope =
  { type: "single", ids: ["014"] }
  { type: "group", ids: ["014", "022", "023"] }
  { type: "public" }
  { type: "agent", id: "environment" }
```

约束：
- 所有人默认始终处于同一公共空间
- `public` 默认广播给当前所有存活者
- 对环境查询时，可直接对环境 agent 调用 `speak(...)`
- 发给环境 agent 的 `text` 是自然语言查询，回复只返回给调用者
- “提问”“回答”“盘问”“解释”不再由额外 `mode` 区分，只由 `text` 语义和当前上下文决定

### 每轮游戏专属动作

见另一个文档。

## 5. 被动接收

`listen` 不是接口。

流程：
1. 某角色调用 `speak(...)`
2. 若目标是环境 agent，则环境 agent 返回定向查询结果给调用者
3. 若目标是 `public`，则所有存活者都能听到
4. 否则，引擎按 `single` 或 `group` 精确分发给目标对象
5. 听者被动接收该信息
6. 信息写入事件流和记忆候选

环境观察不再是独立动作面，而是对环境 agent 的定向 `speak(...)`。
环境 agent 的应答只写入调用者的私有记忆候选，不写入公共事件流。

## 6. 选手 agent

### 输入

每个选手 agent 只看到以下 4 类输入：

1. 自己的背景设定
2. 自己的记忆
3. 一定长度的最近对话
4. 全局信息

所有存活选手在同一阶段获得同规格输入，不因为“戏份”或“重要度”被裁剪。

### 输出

先在心理活动层面产生意图，再调用动作接口。

#### 动作层

```text
actions = [
  {
    action_name,
    args,
    visibility,
    timing
  }
]
```

示例：

```text
actions = [
  {
    action_name: "speak",
    args: {
      target_scope: { type: "single", ids: ["001"] },
      text: "这一轮别跟他走，跟我。"
    },
    visibility: "private",
    timing: "immediate"
  },
  {
    action_name: "end_phase",
    args: {},
    visibility: "none",
    timing: "phase_end"
  }
]
```

除此之外还有每轮游戏专属的一些动作接口，见另一个文档。

## 7. 广播 agent

### 输入

- 当前轮次
- 当前阶段
- 规则文本
- 倒计时
- 结算结果

### 输出

只做制度性文本：
- 宣读规则
- 宣布开始/结束
- 宣布剩余时间
- 公布结果
- 控场

不做：
- 策略建议
- 角色评价
- 隐藏信息泄露

## 8. 环境 agent

### 输入

- 主动给出一些环境信息，比如玩家醒来的时候，告诉他身处什么样的地方
- 负责回应 `speak({ type: "agent", id: "environment" }, text)` 
- 当前公共环境描述
- 当前存活人物概览
- 当前可互动对象
- 当前可用环境查询结果
- 总之，所有的公开信息
- 重点在于细节描写，渲染情绪，而不是机器人一样

## 9. 调度

需要对所有存活选手做全量调度。

规则：
- 不区分前台角色和后台角色
- 每个阶段开始时，所有存活选手都要收到同轮输入并完成一次完整决策
- 公开事件发生后，所有存活选手都要更新自己的记忆和判断
- 私聊或定向事件发生后，只有实际接收者更新对应记忆，但所有存活选手仍保留本阶段自己的行动机会
- 阶段结束前，所有存活选手都必须显式行动或被系统强制 `end_phase()`

## 10. 阶段时钟

- 每个阶段有统一倒计时
- 角色可多次 `speak(...)`
- 角色可提前 `end_phase()`
- 倒计时归零，系统强制所有未结束角色 `end_phase()`