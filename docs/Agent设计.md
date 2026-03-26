# Agent设计

## 1. 范围

MVP 包含 2 类 agent：

1. 选手 agent
2. 广播 agent

玩家不是 agent。游戏引擎也不是 agent。

## 2. 总原则

- agent 的脑内可以自由推理
- agent 对世界的显式行为必须通过接口执行
- 非游戏阶段基础动作只有 `speak(...)` 和 `end_phase()`
- 记忆不是显式接口，agent 不调用“读取记忆”动作，而是在决策时自然使用自己的记忆

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
  { type: "public" }
```

约束：
- 所有人默认始终处于同一公共空间
- `public` 默认广播给当前所有存活者
- 不保留 `group`
- 不保留 `agent` 目标

### 每轮游戏专属动作

见另一个文档。

## 5. 被动接收

`listen` 不是接口。

流程：
1. 某角色调用 `speak(...)`
2. 若目标是 `public`，则所有存活者都能听到
3. 否则，引擎按 `single` 精确分发给目标对象
4. 听者被动接收该信息
5. 信息写入事件流和记忆候选

关键环境文字由系统直接写入公共时间线，不作为独立动作面。

## 6. 选手 agent

### 输入

每个选手 agent 只看到以下 4 类输入：

1. 自己的背景设定
2. 自己的记忆
3. 一定长度的最近对话
4. 全局信息

所有存活选手在同一阶段获得同规格输入，不因为“戏份”或“重要度”被裁剪。

### 输出

先产出隐藏 thought，再调用动作接口。

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

主要做公共时间线内的主持人与制度性文本：
- 宣读规则
- 宣布开始/结束
- 宣布剩余时间
- 公布结果
- 控场
- 与玩家和选手在公共场中互动

不做：
- 策略建议
- 角色评价
- 隐藏信息泄露

## 8. 系统环境文本

环境不作为 agent 存在，而是引擎在关键时间点植入到公共时间线中的系统消息。

要求：
- 只写公共可见信息
- 强调空间感、压迫感、节奏变化和场面状态
- 不承担问答职责
- 不进入对称动作模型

## 9. 调度

需要对所有存活选手做全量调度。

规则：
- 不区分前台角色和后台角色
- 每个阶段开始时，所有存活选手都进入统一世界时钟
- 公开事件发生后，所有存活选手都获得记忆更新机会，但不必立即发言
- 私聊事件发生后，只有实际接收者更新对应记忆
- 每名选手按各自的思考时间自主决定是否行动

## 10. 阶段时钟

- 每个阶段有统一倒计时
- 角色可多次 `speak(...)`
- 角色可提前 `end_phase()`
- 倒计时归零，系统强制所有未结束角色 `end_phase()`
