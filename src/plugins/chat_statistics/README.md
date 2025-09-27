# Chat Statistics Plugin

## 概述

这是一个用于 NoneBot 的聊天统计插件，可以分析用户在群聊中的聊天时间分布。插件基于 `nonebot-plugin-chatrecorder` 的聊天记录数据，提供详细的小时级别聊天统计。

## 功能特性

- 📊 **双重统计模式**: 
  - **消息统计**: 显示用户在24小时内的消息数量分布
  - **活跃统计**: 显示用户在24小时内的活跃天数分布
- 🔢 **百分比显示**: 每个小时的数量和占比
- 📅 **时间范围**: 支持自定义统计天数（默认7天）
- 💾 **数据导出**: 提供JSON格式的数据导出接口
- 🎨 **可视化接口**: 预留可视化扩展接口

## 使用方法

### 基本命令

#### 消息数量统计
```
ttd chat [天数]
```

#### 活跃时间统计
```
ttd active [天数]
```

### 示例

#### 消息统计示例
```
ttd chat          # 查看过去7天的消息统计
ttd chat 3        # 查看过去3天的消息统计
ttd chat 30       # 查看过去30天的消息统计
```

#### 活跃统计示例
```
ttd active        # 查看过去7天的活跃统计
ttd active 10     # 查看过去10天的活跃统计
```

### 输出示例

#### 消息数量统计输出
```
过去7天聊天分布 (共128条消息)
 0h:   1 ( 0.8%)    # 0点发送了1条消息
 1h:   0 ( 0.0%)    # 1点没有发送消息
 2h:   0 ( 0.0%)
...
20h:  12 ( 9.4%)    # 20点发送了12条消息
21h:  10 ( 7.8%)
22h:   8 ( 6.2%)
23h:   3 ( 2.3%)
```

#### 活跃时间统计输出
```
过去10天活跃时间分布 (共4个活跃小时)
 0h:   0 (  0.0%)   # 0点在0天有过发言
 1h:   0 (  0.0%)
...
 9h:   1 ( 10.0%)   # 9点在1天有过发言(10%的天数)
14h:   1 ( 10.0%)   # 14点在1天有过发言
20h:   4 ( 40.0%)   # 20点在4天有过发言(40%的天数)
21h:   2 ( 20.0%)   # 21点在2天有过发言
```

## 依赖项

- `nonebot-plugin-chatrecorder` - 聊天记录存储
- `nonebot-plugin-uninfo` - 用户信息管理
- `nonebot-plugin-orm` - 数据库ORM

## 插件结构

```
src/plugins/chat_statistics/
├── __init__.py          # 插件初始化
├── config.py            # 配置文件
├── statistics.py        # 核心统计逻辑
├── visualization.py     # 可视化接口（预留）
└── __main__.py         # 命令处理器
```

## 核心类说明

### ChatStatistics

主要的统计数据类，包含：

- `hourly_distribution`: 每小时消息数量字典
- `total_messages`: 总消息数
- `get_hourly_percentages()`: 计算百分比分布
- `format_text_output()`: 格式化文本输出
- `to_dict()`: 导出为字典格式

### 主要类和函数

#### 核心类
```python
class ChatStatistics:
    """消息数量统计 - 统计每小时发送的消息数"""
    
class ActiveStatistics:  
    """活跃时间统计 - 统计每小时的活跃天数"""
```

#### 主要函数
```python
# 消息统计函数
async def get_user_chat_statistics(user_id: str, group_id: str, days: int) -> ChatStatistics:
    """获取指定用户的消息统计"""
    
async def get_group_chat_statistics(group_id: str, days: int) -> Dict[str, ChatStatistics]:
    """获取群内所有用户的消息统计"""

# 活跃统计函数  
async def get_user_active_statistics(user_id: str, group_id: str, days: int) -> ActiveStatistics:
    """获取指定用户的活跃统计"""
    
async def get_group_active_statistics(group_id: str, days: int) -> Dict[str, ActiveStatistics]:
    """获取群内所有用户的活跃统计"""
```

## 可视化扩展

插件预留了可视化接口，支持：

1. **JSON导出**: 用于外部可视化工具
2. **ASCII图表**: 在聊天中显示简单图表
3. **图表数据**: 为Chart.js等前端图表库准备数据

### 使用示例

```python
# 导出JSON数据
ChatVisualizationInterface.export_to_json(stats, "user_stats.json")

# 生成ASCII图表
ascii_chart = ChatVisualizationInterface.generate_ascii_chart(stats)

# 准备前端图表数据
chart_data = ChatVisualizationInterface.prepare_chart_data(stats)
```

## 数据隐私

- 插件仅统计消息的时间信息，不存储具体聊天内容
- 所有统计数据基于已有的chatrecorder数据库
- 仅群成员可查看自己的统计数据

## 性能考虑

- 查询使用索引优化，支持大量历史数据
- 统计计算在内存中进行，响应快速
- 支持自定义时间范围，避免不必要的数据处理

## 统计模式对比

| 特性 | 消息统计 (`ttd chat`) | 活跃统计 (`ttd active`) |
|------|---------------------|------------------------|
| **统计内容** | 每小时发送的消息数量 | 每小时有发言的天数 |
| **数值含义** | 消息条数 | 活跃天数 |
| **百分比基准** | 总消息数 | 总统计天数 |
| **适用场景** | 找出最爱聊天的时间段 | 找出最常在线的时间段 |
| **示例** | `20h: 23条 (74.2%)` | `20h: 4天 (40.0%)` |

### 使用建议
- **消息统计**: 适合分析用户的聊天习惯和话痨程度
- **活跃统计**: 适合分析用户的作息规律和在线时间

## 未来扩展

- [ ] Web界面可视化
- [ ] 群组整体统计对比  
- [ ] 更多统计维度（日/周/月）
- [ ] 导出图片格式
- [ ] 活跃度排行榜
- [ ] 两种统计的组合分析

## 开发说明

插件遵循NoneBot2插件开发规范，使用异步编程模式。所有数据库操作通过ORM进行，确保数据安全和一致性。