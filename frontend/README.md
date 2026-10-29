# CampusInbox frontend

Vue 3 + TypeScript + Vite + Element Plus + Pinia + Vue Router。
Node.js 推荐使用仓库 `.nvmrc` 指定的 24 LTS，依赖通过 npm 管理。

```bash
npm ci
npm run dev
```

开发服务器监听 `127.0.0.1:5173`，将 `/api` 代理到 `127.0.0.1:8000`。
当前仅包含环境就绪占位页，尚未实现业务页面。

```bash
npm run lint:check
npm run format:check
npm run build
```

更多说明见仓库根目录 `docs/development.md`。
