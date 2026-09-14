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
- upload bytes thật: `POST /v1/workspaces/{id}/documents`;
- download, rename, move, archive: các endpoint `/v1/documents/*`;
- hỏi đáp có citation: `POST /v1/query`;
- mọi request `/v1/*` được ký SHA-256 cùng timestamp và nonce trước khi gửi.

Frontend không import `packages/` và không truy cập LightRAG/Qdrant trực tiếp.
Mọi business/data access đi qua BE trong `apps/api`.
FE không seed workspace/document/user/config data; trạng thái rỗng hoặc lỗi được
hiển thị cho tới khi BE trả response.
