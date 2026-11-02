# 核对清单区分待处理与来源读取记录

用户核对的6条记录为2条已关联来源和4条已忽略记录，不应被解释为6项待处理任务。此前manual_details统计反映的是来源自动读取完整性，不能等同于用户待办数量。

- 核对清单默认仅显示missing/unrecognized/pending，新增“查看全部来源”切换；已关联、已忽略、归档和清理记录仍可追溯。
- 接口增加scope=attention/all，默认all保持原接口兼容；按状态先过滤再分页，避免已处理来源挤占待处理页。
- 新增attention_count/attention_detail_pending/attention_manual_details及source_total；原manual_details/detail_pending和验收完整性统计不修改，不将忽略/确认操作冒充正文读取成功。
- pending优先级沿用原逻辑，已关联任务收到新变更仍可进入待确认，不因已有任务而隐藏新变更。

33项清单/API/验收测试通过，包含过滤后分页与保留原读取缺口；清单浏览器覆盖默认隐藏2条已关联+4条已忽略、切换全部来源可追溯；同步摘要回归通过。备份后启用新服务，本机接口及页面只读复验：待处理0项、来源6条、原读取缺口统计6，未修改或删除真实任务及来源。启用记录与备份路径见artifacts/inventory-attention-20261023/deployment.json。
