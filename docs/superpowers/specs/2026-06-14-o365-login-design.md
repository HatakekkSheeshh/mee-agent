# O365 Login + Per-user OID cho mee-meeting-agent

**Ngày:** 2026-06-14
**Phạm vi đã chốt:** Login O365 thật (MSAL) + đổi port + truyền OID thật của user đăng nhập xuống pm-agent qua cổng direct-oid hiện có. **Chưa** chuyển sang JWT, **chưa** đóng cổng direct-oid (để bước sau).

---

## 1. Bối cảnh & vấn đề

mee-meeting-agent hiện chạy auth ở chế độ "mock/dev":

- `MicrosoftProvider` ([meeting/auth/microsoft.py](../../../meeting/auth/microsoft.py)) chỉ là stub — `exchange_code` raise `NotImplementedError`. Provider được chọn 1 lần lúc import qua env `AUTH_PROVIDER` (mặc định `mock`).
- Chat endpoint ([meeting/api/chat.py](../../../meeting/api/chat.py)) **không** dùng `get_current_user` mà gọi `repo.get_or_create_dev_user()` → luôn là 1 user cố định (`ms_oid="dev-local-user"`, email `user@vng.com.vn`).
- Khi gọi pm-agent, `pm_agent_client` ([meeting/services/pm_agent_client.py](../../../meeting/services/pm_agent_client.py)) gửi `Authorization: Bearer <TOKEN_AUTHEN_PM_AGENT>` — đây là chỗ đang nhét OID thật của dev vào env. Khớp với cổng test `A2A_TEST_DIRECT_OID` của pm-agent (chấp nhận Bearer = GUID thô). Token này là **singleton toàn cục**, không per-user.

pm-agent ([D:\repo-prod-agentbase\src\a2a_server\server.py](file:///D:/repo-prod-agentbase/src/a2a_server/server.py)) có 2 đường vào trên cùng path `POST /a2a/`:
- **JWT thật:** Bearer là JWT (2 dấu chấm) → validate bằng cách gọi Graph `/me` → lấy `oid`. Cần **Graph access token** của user.
- **Direct-OID (test):** Bearer là GUID thô + env `A2A_TEST_DIRECT_OID=true` → set `user_id = OID` trực tiếp, bỏ qua mọi xác thực.

**Mục tiêu lần này:** Bật login O365 thật để mỗi user đăng nhập có OID thật của họ, rồi gửi đúng OID đó (per-user) sang pm-agent qua cổng direct-oid — thay cho việc hardcode 1 OID tĩnh trong env. Đồng thời đổi port để dùng được callback URL `localhost:8001/auth/callback` mà IT đã đăng ký.

Frontend đã wiring sẵn toàn bộ luồng (LandingPage redirect `/auth/login`, `/auth/me` gate route /onboard/voice + /app) — **không đổi logic FE**, chỉ đổi port.

---

## 2. Kiến trúc sau thay đổi

### 2.1. Port & callback

| Thành phần | Trước | Sau |
|---|---|---|
| Frontend (Vite dev) | `:5173` | **`:8001`** |
| Backend (FastAPI HTTP) | `:8001` | **`:8002`** |
| WebSocket | `:9091` | `:9091` (giữ nguyên) |

Callback đã đăng ký trên Azure: `http://localhost:8001/auth/callback`.

**Luồng callback:** Microsoft redirect browser → `localhost:8001/auth/callback` (Vite) → Vite proxy `/auth/*` → backend `:8002`. Cookie phiên do `/auth/callback` set là host-only (không kèm port) nên hoạt động bình thường ở origin `localhost:8001`; các fetch `/api`, `/auth` sau đó từ React (origin 8001) mang cookie, được proxy về 8002.

**Điểm cần chú ý (redirect_uri):** Vite proxy bật `changeOrigin: true` → host header bị ghi đè thành `localhost:8002`, nên `_redirect_uri(request)` trong [routes.py](../../../meeting/auth/routes.py) sẽ tự dựng sai thành `:8002`. Phải ép `redirect_uri` về đúng giá trị đã đăng ký bằng env **`MS_REDIRECT_URI`** (mặc định `http://localhost:8001/auth/callback`). Cả `get_login_url` lẫn `exchange_code` phải dùng đúng URI này (OAuth bắt buộc 2 lần phải khớp nhau).

### 2.2. Auth provider thật (MSAL)

Triển khai `MicrosoftProvider` bằng `msal.ConfidentialClientApplication`:

- `get_login_url(state, redirect_uri)`: dùng `get_authorization_request_url(scopes, state, redirect_uri)`. Scope: `["User.Read"]` (MSAL tự thêm `openid profile offline_access`). `User.Read` cho Graph token để bước JWT sau này dùng được.
- `exchange_code(code, redirect_uri)`: `acquire_token_by_authorization_code(code, scopes, redirect_uri)`. Lấy `id_token_claims` → trích `oid`, `tid`, `preferred_username`/`email`, `name` → trả `UserInfo(email, display_name, ms_oid=oid, ms_tenant_id=tid)`. Lỗi (thiếu token / `error` trong kết quả) → raise `ValueError` (routes đã bắt → 401).

Cấu hình qua env (đã có sẵn trong code): `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID`, cộng thêm `MS_REDIRECT_URI`. Bật bằng `AUTH_PROVIDER=microsoft`.

> **Lưu ý thư viện:** thêm dependency `msal`. `exchange_code` là hàm sync gọi network bên trong async route — chấp nhận được ở mức dev (block event loop ngắn). Tối ưu hoá (chạy threadpool) để sau, không làm bây giờ.

> **Deferred seam:** MSAL trả cả access_token (Graph) + refresh_token. Lần này **không lưu** (YAGNI cho phạm vi direct-oid). Khi chuyển sang JWT sẽ lưu refresh_token (cột `User.refresh_token` đã có) để cấp lại Graph token.

### 2.3. Per-user OID xuống pm-agent

Hiện chat dùng dev user + OID tĩnh trong env. Đổi để dùng OID thật của user đăng nhập:

1. **Chat endpoints** ([chat.py](../../../meeting/api/chat.py) `send_message`, `send_message_stream`): thay `repo.get_or_create_dev_user(session)` → `Depends(get_current_user)` (route `/messages/stream` không nhận được Depends do tự mở session trong generator → resolve user trước khi vào `StreamingResponse`, hoặc đọc cookie thủ công bằng `get_current_user_optional`). User chưa đăng nhập → 401 (đúng ý đồ gate login).
2. **Truyền OID xuống graph:** thêm field `pm_user_oid: Optional[str]` vào `ChatState` ([_chat_state.py](../../../meeting/graphs/_chat_state.py)). `run_chat_turn` / `stream_chat_turn` / `resume_chat_turn` ([runner.py](../../../meeting/graphs/chat_graph/runner.py)) nhận thêm tham số `pm_user_oid` và đưa vào `initial_state`. (resume đọc lại từ checkpoint nên chỉ cần set lúc tạo turn đầu.)
3. **pm_call** ([pm.py](../../../meeting/graphs/chat_graph/pm.py)): đọc `state["pm_user_oid"]` và truyền xuống client cho mỗi lần `send_message`.
4. **pm_agent_client:** `send_message(...)` nhận thêm `bearer: Optional[str] = None`; khi có thì `_rpc` dùng `Authorization: Bearer <bearer>` (và bỏ/giữ X-API-KEY tuỳ) thay cho `self._api_key`. Khi `bearer=None` → giữ hành vi cũ (tương thích ngược, không vỡ test). Như vậy OID thật của user đi theo từng request thay vì token tĩnh.

Kết quả: mỗi user chat → pm-agent nhận đúng OID của họ qua cổng direct-oid. pm-agent vẫn giữ `A2A_TEST_DIRECT_OID=true` cho tới khi ta chuyển sang JWT ở bước sau.

> Dev user `get_or_create_dev_user` (`ms_oid="dev-local-user"`) **không** phải GUID nên sẽ không qua được direct-oid của pm-agent — đây là lý do phải dùng user thật. Giữ lại hàm dev user cho test/fallback nhưng không dùng trong luồng chat đã gate.

---

## 3. Luồng dữ liệu (end-to-end)

```
[Browser :8001] --click login--> /auth/login (Vite→:8002)
   → MicrosoftProvider.get_login_url(redirect_uri=MS_REDIRECT_URI) → 302 login.microsoftonline.com
[Microsoft] --redirect--> :8001/auth/callback?code&state (Vite→:8002)
   → validate state(CSRF) → MicrosoftProvider.exchange_code(code) [MSAL]
   → UserInfo(email, oid, tid) → _upsert_user (lưu ms_oid/ms_tenant_id)
   → set cookie phiên → 302 /onboard/voice (lần đầu) hoặc /app
[Chat] POST /api/chat/.../messages  (cookie phiên)
   → get_current_user → user.ms_oid
   → run_chat_turn(pm_user_oid=user.ms_oid)
   → pm_call → client.send_message(bearer=user.ms_oid)
   → POST :pm-agent /a2a/  Authorization: Bearer <oid>  (direct-oid path)
```

---

## 4. Danh sách file thay đổi

**Code:**
- `meeting/auth/microsoft.py` — triển khai MSAL thật.
- `meeting/auth/routes.py` — `_redirect_uri` ưu tiên env `MS_REDIRECT_URI`.
- `meeting/services/pm_agent_client.py` — `send_message`/`_rpc` nhận `bearer` per-call.
- `meeting/graphs/_chat_state.py` — thêm `pm_user_oid`.
- `meeting/graphs/chat_graph/runner.py` — 3 hàm turn nhận + set `pm_user_oid`.
- `meeting/graphs/chat_graph/pm.py` — đọc `pm_user_oid`, truyền `bearer`.
- `meeting/api/chat.py` — dùng user đăng nhập thật thay dev user.

**Config / port:**
- `meeting_frontend_react/vite.config.ts` — `server.port: 8001`; proxy `/api`,`/auth` → `http://localhost:8002`, `/ws` → `:9091`.
- `run_meeting.py` — `--http-port` default `8002`.
- `whisper_live/backend/maas_backend.py` — `BACKEND_URL` default `http://127.0.0.1:8002` (đang trỏ 8001).
- `.env` / `.env.example` — `AUTH_PROVIDER=microsoft`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID`, `MS_REDIRECT_URI=http://localhost:8001/auth/callback`.
- `requirements` — thêm `msal`.

**Docs (cập nhật port):** `CLAUDE.md`, `README.md`, `meeting_frontend_react/README.md`, comment trong `vite.config.ts` & `src/api/client.ts`.

---

## 5. Xử lý lỗi

- Sai/hết hạn state CSRF → 400 "Invalid or expired state" (đã có sẵn).
- `exchange_code` thất bại → `ValueError` → routes trả 401.
- Chat khi chưa đăng nhập → 401 từ `get_current_user`; FE đã có sẵn xử lý 401 → về landing.
- pm-agent từ chối OID (vd GUID không hợp lệ) → `PmAgentError` → node `pm_error` interrupt mời thử lại (đã có sẵn).
- `MS_CLIENT_ID/SECRET` thiếu khi `AUTH_PROVIDER=microsoft` → `MicrosoftProvider.__init__` raise rõ ràng lúc khởi động (đã có sẵn).

## 6. Kiểm thử

- **Unit:** `MicrosoftProvider.exchange_code` với MSAL app được mock → trả đúng `UserInfo`. `pm_agent_client.send_message(bearer=...)` → assert header `Authorization: Bearer <oid>` (dùng `httpx.MockTransport`, đã có pattern). Test cũ (bearer=None) vẫn xanh.
- **Integration:** turn chat với `pm_user_oid` set → pm_call dùng đúng bearer.
- **Manual (e2e):** `AUTH_PROVIDER=microsoft`, backend `:8002`, FE `:8001` → đăng nhập bằng tài khoản VNG thật → kiểm tra user lưu đúng `ms_oid`/`ms_tenant_id` → chat 1 lệnh pm → xác nhận pm-agent (log direct-oid) nhận đúng OID của user.

## 7. Ngoài phạm vi (bước sau)

- Chuyển Bearer sang Graph JWT thật → dùng cổng JWT của pm-agent.
- Lưu + refresh token (Graph) cho user.
- Đóng cổng `A2A_TEST_DIRECT_OID` bên pm-agent.
- Siết CORS backend (hiện `allow_origins=["*"]`).
