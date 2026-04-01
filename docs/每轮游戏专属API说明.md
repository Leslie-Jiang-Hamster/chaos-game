# 每轮游戏专属 API 说明

## 1. 目的

本文只定义五轮游戏中的专属动作接口。

不包含：
- 基础动作 `speak(target_scope, text)`
- 基础动作 `end_phase()`
- 主持人与系统环境文本的行为细节（含“环境不可私聊、仅转场固定文本、不接 LLM”）
- 各轮完整数值平衡与文案表现

本文的作用是把“每轮游戏里角色到底能调用什么动作、参数怎么写、何时可调、引擎如何校验”固定下来。

## 2. 通用约定

### 2.1 动作调用格式

所有专属动作都按统一格式进入引擎：

```text
{
  action_name: "...",
  args: { ... }
}
```

示例：

```text
{
  action_name: "choose_number",
  args: {
    value: 37
  }
}
```

### 2.2 通用校验规则

- 只有当前 `available_actions` 中出现的动作才允许调用
- 参数缺失、类型错误、越界、重复提交，均由引擎拒绝
- 除非该轮规则明确允许重复提交，否则同一动作在同一阶段默认只提交一次
- 默认目标只能是当前仍存活的合法对象
- 专属动作默认不向其他选手实时公开，是否公开由本轮结算规则决定

### 2.3 ID 与基础类型

```text
contestant_id = "001" ~ "010"（当前对局）
side = "left" | "right"
```

说明：
- `contestant_id` 使用三位字符串
- 玩家也是合法 `contestant_id`
- 轮到双人局时，目标集合会被引擎自动缩小到该局允许的对象
- 完整角色池可超过 10 人，但单局只会选入 10 人参赛

### 2.4 当前赛制人数

```text
10 -> 8 -> 6 -> 4 -> 2 -> 1
```

规则：
- 前 4 轮每轮淘汰 2 人
- 第 5 轮 2 进 1

## 3. 第 1 轮：诱饵均值

### 3.1 阶段开放

```text
game.available_actions = [
  "speak",
  "choose_number",
  "end_phase"
]
```

### 3.2 `choose_number(value)`

签名：

```text
choose_number(value)
```

参数：

```text
value: integer
```

校验：
- `value` 必须是整数
- 取值范围必须在 `0..100`
- 每名存活选手在本轮游戏执行阶段只能提交 1 次

可见性：
- 秘密提交
- 在结算前不对其他选手公开

引擎记录：

```text
submitted_number[player_id] = value
```

结算用途：
- 计算全体真实平均值
- 目标数 = 平均值的 `1/2`
- 计算每名选手与目标数的距离
- 选出前 8 名存活者

示例：

```text
choose_number(37)
choose_number(0)
choose_number(100)
```

## 4. 第 2 轮：镜桥同行

### 4.1 阶段开放

```text
game.available_actions = [
  "speak",
  "choose_partner",
  "choose_order",
  "choose_path",
  "end_phase"
]
```

### 4.2 `choose_partner(target_id)`

签名：

```text
choose_partner(target_id)
```

参数：

```text
target_id: contestant_id
```

校验：
- `target_id` 必须是除自己以外的存活选手
- 每名选手只能提交 1 次配对选择
- 一旦配对锁定，本动作不可再次调用

可见性：
- 默认秘密记录
- 配对锁定时由系统统一公布结果

引擎记录：

```text
partner_choice[player_id] = target_id
```

说明：
- 配对锁定算法由引擎决定
- 本接口只负责表达“我希望与谁配对”

### 4.3 `choose_order(mode)`

签名：

```text
choose_order(mode)
```

参数：

```text
mode = "self_first" | "other_first"
```

校验：
- 只有已完成配对的角色才能调用
- 必须在当前桥段尚未结算时提交
- 每一桥段每名角色只能提交 1 次

可见性：
- 对组内结算可见
- 是否实时公开给搭档，由具体桥段流程决定

引擎记录：

```text
bridge_segment_order[segment_index][player_id] = mode
```

说明：
- 该动作只决定“谁先走”
- 不决定走哪一侧

### 4.4 `choose_path(side)`

签名：

```text
choose_path(side)
```

参数：

```text
side = "left" | "right"
```

校验：
- 只有当前桥段允许选路时才能调用
- 每个需要行动的角色在当前桥段只能提交 1 次

可见性：
- 组内可见性由桥段流程决定
- 默认不向其他组公开

引擎记录：

```text
bridge_segment_path[segment_index][player_id] = side
```

结算用途：
- 结合该组选手掌握的桥面信息
- 结合当前桥段先后顺序
- 推进桥段生死结果

示例：

```text
choose_partner("008")
choose_order("self_first")
choose_path("left")
```

## 5. 第 3 轮：四门筹码

### 5.1 阶段开放

```text
game.available_actions = [
  "speak",
  "assign_tokens",
  "end_phase"
]
```

### 5.2 `assign_tokens(distribution)`

签名：

```text
assign_tokens(distribution)
```

参数：

```text
distribution = {
  gate_1: integer,
  gate_2: integer,
  gate_3: integer,
  gate_4: integer
}
```

校验：
- `gate_1..gate_4` 必须全部出现
- 每个值必须是非负整数
- 四项总和必须等于 `10`
- 每名选手只能提交 1 次

可见性：
- 秘密提交
- 结算前不公开

引擎记录：

```text
token_assignment[player_id] = distribution
```

结算用途：
- 统计每扇门收到的筹码
- 每门最高者获胜
- 同一人最多赢 1 门
- 依据引擎预先定义的平局规则选出生还 4 人

示例：

```text
assign_tokens({
  gate_1: 4,
  gate_2: 3,
  gate_3: 2,
  gate_4: 1
})
```

## 6. 第 4 轮：钥匙与刀

### 6.1 阶段开放

```text
game.available_actions = [
  "speak",
  "give_key_to",
  "give_blade_to",
  "end_phase"
]
```

### 6.2 `give_key_to(target_id)`

签名：

```text
give_key_to(target_id)
```

参数：

```text
target_id: contestant_id
```

校验：
- `target_id` 必须是当前仍存活的合法对象
- 每名选手在本轮只能交出 1 把钥匙
- 不可重复提交

可见性：
- 秘密提交

引擎记录：

```text
key_target[player_id] = target_id
```

### 6.3 `give_blade_to(target_id)`

签名：

```text
give_blade_to(target_id)
```

参数：

```text
target_id: contestant_id
```

校验：
- `target_id` 必须是当前仍存活的合法对象
- 每名选手在本轮只能交出 1 把刀
- 不可重复提交

可见性：
- 秘密提交

引擎记录：

```text
blade_target[player_id] = target_id
```

结算用途：
- 汇总每人获得的钥匙数与刀数
- 依据该轮评分规则计算结果
- 前 2 名存活

示例：

```text
give_key_to("003")
give_blade_to("008")
```

## 7. 第 5 轮：终局审问

第 5 轮分为 3 个可调用阶段：
- 预备阶段
- 审问阶段
- 猜测阶段

### 7.1 预备阶段开放

```text
prepare.available_actions = [
  "choose_die_face"
]
```

### 7.2 `choose_die_face(value)`

签名：

```text
choose_die_face(value)
```

参数：

```text
value: integer
```

校验：
- `value` 必须在 `1..6`
- 每名角色在当前审问循环开始前只能提交 1 次
- 一旦双方都提交，本轮固定，不可更改

可见性：
- 秘密提交

引擎记录：

```text
die_face[player_id] = value
```

### 7.3 审问阶段开放

```text
interrogation.available_actions = [
  "speak",
  "end_phase"
]
```

说明：
- 审问阶段不新增专属发言动作
- 提问和回答都通过普通 `speak(...)` 完成
- 引擎需要额外追踪每名角色已经给出的回答数量与真假配额

引擎内部至少应维护：

```text
answer_count[player_id]
true_answer_count[player_id]
false_answer_count[player_id]
```

规则约束：
- 每名角色对自己给出的 4 个回答必须满足 `2 真 2 假`
- 双方都可多轮提问
- 点数在本轮审问中保持不变

### 7.4 猜测阶段开放

```text
guess.available_actions = [
  "guess_die_face",
  "pass_guess",
  "end_phase"
]
```

### 7.5 `guess_die_face(target_id, value)`

签名：

```text
guess_die_face(target_id, value)
```

参数：

```text
target_id: contestant_id
value: integer
```

校验：
- `target_id` 必须是当前对手
- `value` 必须在 `1..6`
- 每名角色在当前猜测阶段最多猜 1 次

可见性：
- 公开动作
- 引擎立即结算

结算：
- 猜对：对手死亡或失败
- 猜错：调用者立即死亡或失败

### 7.6 `pass_guess()`

签名：

```text
pass_guess()
```

参数：

```text
无
```

校验：
- 只能在猜测阶段调用
- 每名角色在当前猜测阶段最多调用 1 次

可见性：
- 公开动作

结算：
- 若双方都 `pass_guess()`，则进入下一轮审问
- 点数保持不变

示例：

```text
choose_die_face(5)
guess_die_face("010", 2)
pass_guess()
```

## 8. 引擎最少需要提供的能力

为支撑以上 API，引擎至少要能做到：

- 根据轮次和阶段生成正确的 `available_actions`
- 拒绝不在当前阶段开放的动作
- 拒绝非法参数、重复提交和非法目标
- 区分秘密提交、组内可见、公开动作三种可见性
- 在结算阶段读取该轮专属动作记录并推进生死结果
- 把结果写回公共事件流和各角色私有记忆

## 9. 待后续细化但不影响当前接口的部分

以下内容暂不影响动作名和参数形状，可在后续规则文档中继续细化：

- 第 2 轮配对锁定算法
- 第 2 轮每一桥段的信息分配与死亡判定
- 第 3 轮平局规则
- 第 4 轮钥匙与刀的精确计分公式
- 第 5 轮审问阶段“回答”如何被引擎标记为真或假
