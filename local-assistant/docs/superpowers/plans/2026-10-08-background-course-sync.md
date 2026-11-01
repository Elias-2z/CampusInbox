# Background course notification sync

User decision: first QR sign-in initiated inside CourseInbox; afterwards no manually open Chaoxing website or Android companion. Preserve current tasks and original notice integration. A session still exists locally and can expire. Start with course notifications and instruction attachments, not all historical chats.

Approach: a dedicated headless Edge session controlled by the application's own Node/Playwright worker. The Python server manages its process, persists enablement and text-only intake queue, invokes the existing document extractor and notice validator, and retains original evidence. A local authenticated QR image/status/control API powers a minimal sync page. Reuse the observed official inbox/detail DOM parser. Open official login only in this dedicated profile; never read an existing browser's cookies. The application browser session is sealed using current-user Windows DPAPI under ignored data; no raw cookie/profile is persisted, and never enter status/API responses, archives or logs.

1. RED: meaningful tests for manager intake/deduplication, pause, local-only control and secret-free status; implement lifecycle and persistent queue.
2. Add Node worker: official QR UI, session reuse, rendered inbox collection, bounded downloads, cancelation and one-minute background scans. Source login/DOM/network failure must be visible.
3. Replace main desktop sync flow with connect/QR/status/pause; keep extension as collapsed alternative. Add launch/dependency instructions.
4. Run focused checks and one review, then load the final server. Show a real fresh official QR for user sign-in. Human scanning and subsequent authenticated continuous sync remain a live acceptance boundary until actually observed. Stop checks once useful verification passes; no repeated broad test loops.

No commit or push. Existing modifications and formal data remain. Use installed runtime Node/Playwright; make dependency discovery portable. All control and QR routes are local-only and require the main local API token, never the browser extension token. No account credentials or password fields in CourseInbox.
