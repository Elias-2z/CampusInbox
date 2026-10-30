# CampusInbox frontend

Vue 3 + TypeScript + Vite + Pinia + Vue Router。沿用已确认的活力校园设计：紫色 / 青柠 / 便签黄和 emoji，响应式布局。Element Plus 保留为可用依赖，现有业务界面采用产品专属样式与原生可访问控件。

```bash
npm ci
npm run dev
```

127.0.0.1:5173 将 /api 代理到本机 8000。首页、收件箱、任务与模型设置均连接真实 API；不预置演示数据。导入对话框可主动试填示例。

```bash
npm run lint:check
npm run format:check
npm run build
```

更多说明见仓库根目录 README.md 和 docs/。
