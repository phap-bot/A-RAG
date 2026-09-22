# Frontend Knowledge Base

Frontend độc lập dùng React, TypeScript và Vite. UI được tổ chức theo flow
`home → workspaces → project → AI Assistant`, bám theo project Stitch
`AI Document Workspace`.

## Chạy local

```powershell
Copy-Item .env.example .env
npm install
npm run dev
```

Backend A-RAG phải chạy tại `http://127.0.0.1:8010` (MinerU API giữ ở
`http://127.0.0.1:8000`). Có thể đổi địa chỉ bằng `VITE_API_BASE_URL` trong
`web/.env`.

Frontend hiện chỉ dùng dữ liệu từ backend và nối các feature theo endpoint:

- shell/user/locale: `GET /v1/ui/bootstrap`, `PUT /v1/preferences/language`;
- workspace và server-side search: `GET /v1/workspaces`;
- overview, members, settings: `GET /v1/workspaces/{id}/{section}`;
- tài liệu và server-side search: `GET /v1/documents`;
- upload bytes thật: `POST /v1/workspaces/{id}/documents` (trả `uploaded`, chưa parse);
- bắt đầu ingestion: `POST /v1/documents/actions/sync-up` (chuyển `processing` → `indexed` hoặc `failed`);
- theo dõi job: `GET /v1/ingestion/jobs/{job_id}` và `GET /v1/ingestion/status`;
- theo dõi execution graph: `GET /v1/ingestion/jobs/{job_id}/events?workspace_id=...&after=0`, trả event có sequence, node, namespace, duration và progress an toàn cho UI;
- download, rename, move, archive: các endpoint `/v1/documents/*`;
- hỏi đáp Agentic RAG realtime: `POST /api/chat/stream` (SSE), gồm activity
  của node/tool, text delta, citation, confidence và provenance ở event cuối;
- `POST /v1/query` vẫn giữ cho client đồng bộ/legacy, còn hai màn hình chat
  chính dùng SSE để không chạy graph lần thứ hai;
- mọi request `/v1/*` được ký SHA-256 cùng timestamp và nonce trước khi gửi.

Frontend không import `packages/` và không truy cập LightRAG/Qdrant trực tiếp.
Mọi business/data access đi qua BE trong `apps/api`.
FE không seed workspace/document/user/config data; trạng thái rỗng hoặc lỗi được
hiển thị cho tới khi BE trả response.
FE không sử dụng `localStorage` hoặc `sessionStorage`. Auth và UI state chỉ tồn
tại trong React memory; navigation dùng URL không chứa dữ liệu nhạy cảm nên
reload vẫn mở lại đúng workspace/section/document và tải dữ liệu từ BE. Mật
khẩu chỉ tồn tại trong state tạm thời của form và được gửi qua HTTPS tới BE để
BE băm bằng Argon2id.

Client SSE dùng `fetch` có ký SHA-256 giống các request JSON. Mỗi frame được
parse theo ranh giới SSE, ghép `message_chunk.content` theo thứ tự nhận được,
hiển thị `agent_thought`/tool activity, rồi lấy citation và chat session từ
event hoàn tất. Vì vậy một câu hỏi chỉ tạo một lần chạy LangGraph.
