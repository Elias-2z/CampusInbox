# CampusInbox UI Integration Implementation Plan

**Goal:** 在独立副本中使用 Elias-2z/CampusInbox 的 Vue 界面与视觉体系，保留本项目全部业务能力。
**Architecture:** Python 服务与数据库不变。Vue 直接调用原 API；首页、任务、日历、提醒与设置使用新版组件；复杂审核、同步、手机连接、评测与详情复用原工作区，通过同源嵌入、统一样式和事件桥接整合。提供原工作区独立入口。
**Tech Stack:** Vue 3 + TypeScript + Vite; Python stdlib + SQLite.
**Spec:** 聊天中已确认的七步方案；用户指示直接完成。

## Global Constraints
- 只修改迁移副本，不修改原项目。
- 保留 done/submitted、scope、requirements、date_precision、审核与鉴权。
- 使用临时数据库和合成通知验收。
- 构建到 web/ui，由 Python 服务静态提供，普通启动无需 Node。
- 保留 /legacy.html 独立入口；Android 接口保持兼容。

## Review Focus
- 中文路径、桌面启动、原项目端口冲突。
- 日期精度、北京时间跨日、无截止日期。
- 远程令牌、同源嵌入消息校验。
- 审核未保存输入与后台刷新。
- 窄屏导航与详情滚动。

## Tasks
- [x] 1. 原 Python 测试基线；静态路由测试先失败。
- [x] 2. 安全静态服务和旧工作区入口，通过路由测试。
- [x] 3. 上游视觉资源、Vue 配置；日期状态测试先失败后实现适配。
- [x] 4. 实现 Vue 页面与完整工作区桥接。
- [x] 5. 构建；临时库下导入、确认、状态、材料、日历、窄屏验收。
- [x] 6. 回归与桌面验证、启动说明、原项目摘要核验。

## Execution ledger
- 独立副本已建立，分支 ui-integration；不再另建 worktree。
- 原后端测试在代码修改前已启动。

- 完成：661 项 Python 测试、桌面测试、前端检查与构建、新增浏览器回归通过；原项目 516 个文件 SHA-256 一致。
- 交付留在用户指定副本，不合并回原项目、不上传。
