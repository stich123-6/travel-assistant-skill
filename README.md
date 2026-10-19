# 行程安排助手

供团队在 WorkBuddy 共享项目中使用的 Skill，当前版本 **1.1.1**。

支持外地出行、本市展会会议、备忘提醒、钉钉通知，以及常用出发地点的新增、修改、删除和卡片更新。没有说明本次出发地时先询问；手机确认后的系统日历写入保留待接入步骤。

## 在 WorkBuddy 中使用

1. 下载 [Skill 安装包](travel-assistant-v1.1.1.zip)，在 WorkBuddy 技能管理中上传导入。
2. 按[团队项目配置](publish/WorkBuddy团队项目配置.md)创建“行程安排助手”项目，粘贴[项目指令](publish/WorkBuddy项目指令.md)，绑定本 Skill 和所需依赖。
3. 邀请团队成员加入，在项目内新建自己的任务直接提出需求。

依赖为腾讯地图 `tencentmap-map-assistant`、同程程心 `tc-chengxin`、滴滴 `didi-ride-skill`、钉钉 `dingtalk-unified`。本仓库不包含这些第三方 Skill 或其授权凭证。

仓库用于团队内部保存和分发，访问需有 GitHub 仓库读取权限。GitHub 上传不代表已上架 WorkBuddy 技能市场，也不代表已创建 WorkBuddy 项目。

## 使用示例

- “明天下午两点去会展中心开会，帮我安排。”
- “下周三从公司去杭州参展，上午九点前到，先查高铁。”
- “增加默认出发地点：杭州东站。”
- “修改公司出发地点。”
- “删除默认出发地点杭州东站。”
- “现在钉钉告诉张三，明天下午三点开会。”

## 当前接入条件

成员个人业务使用本人授权；地点记忆需要已有 Python 运行时、宿主可信用户身份和成员私有持久存储。用户资料不写入安装包或共享项目资产。

原生输入框和卡片刷新取决于 WorkBuddy 实际交互工具。系统日历尚未接入；提醒、定时消息和真实业务调用以可用工具及实际回执为准。配置材料不代表移动端或供应商接口已完成联调。

## 本地验证

在仓库根目录运行：

```text
python -X utf8 -B validation/test_origin_memory.py
```

覆盖9个持久记忆测试。完整的对话验收预期见[场景验收](validation/场景验收.md)。

Skill 入口为 [travel-assistant/SKILL.md](travel-assistant/SKILL.md)。
