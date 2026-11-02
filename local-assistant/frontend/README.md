# Integrated Vue frontend

See [integration instructions](../docs/ui-integration.md).

- `npm ci` installs locked dependencies.
- `npm run build` type-checks and writes production assets to `../web/ui`.
- `npm run lint:check`, `npm run format:check`, and `npm test` validate source and date/status semantics.
- Upstream visual source: Elias-2z/CampusInbox, commit 6307102e6b6f895127584c7ae6e09b39b9fa0674.
- The Python service is authoritative for data, authentication, review and transactions.
