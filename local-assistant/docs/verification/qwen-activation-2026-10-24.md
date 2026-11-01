# Qwen桌面启动接入验证

启动时发现Ollama已运行、4B模型已安装、根目录.env配置正确，但服务仍是rules模式。完整重启后仍复现，原因是WindowsServiceEnvironment直接启动server.py，没有继承start.ps1对.env模型配置的读取。

修复：桌面启动子进程前读取项目.env，仅导入CI_MODEL_PROVIDER、CI_MODEL_BASE_URL、CI_MODEL、CI_MODEL_API_KEY四个模型字段，支持引号和空白，不导入PATH等任意配置，不修改系统环境。

验证：新增桌面配置读取测试先因缺少实现失败，补齐后48项Windows桌面测试通过。正式服务重启后/api/state报告agent/ollama/qwen3:4b-instruct。虚构课程通知在临时SQLite内完成一次真实Agent调用，约21.08秒，无规则回退，提取一条截止2026-10-30 18:00的候选，未确认任何任务。单次测量不能代表识别准确率或持续处理延迟。

重启前备份data/backups/before-qwen-activation-20261024-191754/，恢复同步前消息、任务、草稿、来源、历史五表哈希全部保持一致。调用证据与部署记录在artifacts/qwen-activation-20261024/。网页导入时仍需选择“规则 + 模型辅助”，模型不能自动确认任务。
