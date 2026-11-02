# 学习通网页正文接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Follow the user's existing instruction to implement directly in this checkout.

**Goal:** 将用户已登录学习通网页当前会话中的文字同步到 CourseInbox，能够查看真实正文。

**Architecture:** Edge / Chrome Manifest V3 组件读取官方 webim 页面中的消息节点，后台使用仅能写入学习通正文的配对令牌发送到本机专用接口。稳定消息编号去重，保留会话、发件人和网页时间标记。登录态保留在浏览器。

**Tech Stack:** Python 标准库、现有 SQLite、JavaScript、Manifest V3。

**Spec:** 用户在当前会话要求直接实现学习通消息内容查看，并已登录官方个人空间；当前真实网页为 fe.chaoxing.com/front/webim/index.html，正文节点为 .message-row[data-msg-id] .bubble-text。

## Global Constraints

- 保留现有未提交修改，直接继续当前 checkout；不提交或发布。
- 仅同步当前已打开聊天会话的文字；不自动切换会话或发送消息。
- 网页时间无法识别时保留原标记，展示时间未知且不自动提取相对时间任务。
- 扩展默认关闭，用户配置并启用后才同步；只向 127.0.0.1:8765 写入。
- 不读取学习通 Cookie、密码或应用内隐藏状态。
- 不把旧的 Android 摘要与网页正文按时间强行合并，防止关联错误。

## Review Focus

- 同一消息重新打开页面、跨日重试仍只保存一次。
- 相同正文的不同消息编号分别保存。
- 学习通聊天以外的网页或正文以外附件不误采集。
- 配对令牌不能读取任务或其他消息，非本机和其他网页来源不能写入。
- 网络失败保留队列，成功或重复确认后才删除；未知时间不伪造发布时间。

## Task 1: 接收接口与元数据

**Files:** courseinbox/chaoxing.py、courseinbox/http.py、courseinbox/core.py、tests/test_chaoxing.py。

- [x] 写入并运行失败测试：正文保存、稳定编号去重、元数据、令牌权限隔离、来源和本机限制、未知时间不产生草稿。
- [x] 实现 normalize_web_message(body)、scoped_token(token)、POST /api/chaoxing/messages、GET /api/chaoxing/pairing。
- [x] 运行新测试及已有 Python 测试，确认无回归。

## Task 2: 浏览器组件

**Files:** browser-companion/manifest.json、parser.js、content.js、background.js、options.html/js/css、README.md；scripts/test-chaoxing-web.cjs、scripts/package-browser-companion.py。

- [x] 写入并运行失败测试：北京时间解析、跨年、未知时间、正文转换、去重和失败保留队列。
- [x] 实现页面监听、编号去重、持久队列、重试、配置启用和同步状态。
- [x] 打包 zip 并检查 manifest、脚本语法和单元测试。

## Task 3: 网页入口与真实验收

**Files:** web/app.js、README.md、docs/API.md、docs/verification/chaoxing-web.md。

- [x] 手机连接页增加网页组件下载、配对令牌、状态和安装指导；消息详情显示会话和发件人，未知时间明确标注。
- [x] 用浏览器当前真实消息进行一次正文读取及专用接口导入，并核对 CourseInbox 详情。
- [x] 区分一次真实正文验证、组件单元验证和浏览器安装后的连续同步验证；可审阅成品和安装说明已就绪。
- [ ] 用户在 Edge / Chrome 加载并启用组件后，验收新消息连续同步。

## 审查与执行记录

独立审查发现专用写入令牌在重放响应中可读原文、未知时间可通过重新提取误算截止日期、规则提取失败堵塞队列及关闭开关仍继续发送。均先复现失败回归再修复，最终61 Python与5 Node测试通过。

Ruling: 使用本机回环专用写入接口，直接继续用户指定 checkout；保留用户已有改动且不提交。Codex 内置浏览器无法安装组件，因此其中完成一次真实正文验收，持续采集由用户在 Edge / Chrome 安装后继续验收。记录见 docs/verification/chaoxing-web.md。
