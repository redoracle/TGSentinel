# **TG Sentinel Developer Panel — Comprehensive Audit Report**

**Panel:** Integration Settings + Webhooks + Integration Console  
**Date:** 2025-11-17  
**Auditor:** AI Engineering Assistant  
**Methodology:** Pixel-perfect element enumeration + API binding verification + real-time behavior + security analysis

---

## **A. Element-by-Element Audit**

### **Integration Settings Section**

| Element                   | Type                    | Triggered Endpoint        | Method | Auth    | Request Payload                            | Response Schema             | UI Update                 | Errors           | Security                        | Risk     | Status |
| ------------------------- | ----------------------- | ------------------------- | ------ | ------- | ------------------------------------------ | --------------------------- | ------------------------- | ---------------- | ------------------------------- | -------- | ------ |
| **Prometheus Port Label** | `<label>`               | N/A                       | N/A    | N/A     | N/A                                        | N/A                         | N/A                       | N/A              | N/A                             | Low      | ✅     |
| **Prometheus Port Input** | `<input type="number">` | `/api/developer/settings` | POST   | Session | `{"prometheus_port": 9100}`                | `{"status": "ok"}`          | Toast shown               | 400 if invalid   | Range validation                | Medium   | ✅     |
| **API Key Label**         | `<label>`               | N/A                       | N/A    | N/A     | N/A                                        | N/A                         | N/A                       | N/A              | N/A                             | Low      | ✅     |
| **API Key Input**         | `<input readonly>`      | None (client-side only)   | N/A    | N/A     | N/A                                        | N/A                         | Masked display            | None             | Readonly prevents tampering     | Medium   | ✅     |
| **Generate Button**       | `<button>`              | None (client-side)        | N/A    | N/A     | N/A                                        | N/A                         | Enables Copy, shows mask  | Web Crypto error | Uses `crypto.getRandomValues()` | **HIGH** | ✅     |
| **Copy Button**           | `<button>`              | None (clipboard API)      | N/A    | N/A     | N/A                                        | N/A                         | Clears key, disables self | Clipboard error  | Immediate wipe after copy       | Medium   | ✅     |
| **Helper Text**           | `<small>`               | N/A                       | N/A    | N/A     | N/A                                        | N/A                         | N/A                       | N/A              | User education                  | Low      | ✅     |
| **Open Docs Button**      | `<button>`              | `/docs`                   | GET    | None    | N/A                                        | HTML                        | Opens new tab             | None             | Public endpoint                 | Low      | ✅     |
| **Save Button**           | `<button>`              | `/api/developer/settings` | POST   | Session | `{"prometheus_port": N, "api_key": "..."}` | `{"status": "ok"}` or error | Toast + console log       | 400/500 handled  | Validates port range            | **HIGH** | ✅     |

### **Webhooks Section**

| Element                     | Type                          | Triggered Endpoint             | Method | Auth    | Request Payload              | Response Schema                                                 | UI Update                   | Errors                    | Security                 | Risk         | Status |
| --------------------------- | ----------------------------- | ------------------------------ | ------ | ------- | ---------------------------- | --------------------------------------------------------------- | --------------------------- | ------------------------- | ------------------------ | ------------ | ------ |
| **Webhooks Header**         | `<h2>`                        | N/A                            | N/A    | N/A     | N/A                          | N/A                                                             | N/A                         | N/A                       | N/A                      | Low          | ✅     |
| **Add Webhook Button**      | `<button>`                    | None (shows form)              | N/A    | N/A     | N/A                          | N/A                                                             | Form visibility toggle      | None                      | Client-side              | Low          | ✅     |
| **Service Name Input**      | `<input required>`            | `/api/webhooks`                | POST   | Session | `{"service": "slack", ...}`  | `{"status": "ok", "service": "..."}`                            | Reloads table               | 409 on duplicate          | Duplicate check          | Medium       | ✅     |
| **Webhook URL Input**       | `<input type="url" required>` | `/api/webhooks`                | POST   | Session | Part of webhook payload      | Error on invalid                                                | Reloads table               | **400 if invalid format** | **URL regex validation** | **HIGH**     | ✅     |
| **Secret Input**            | `<input type="password">`     | `/api/webhooks`                | POST   | Session | Optional `{"secret": "..."}` | N/A                                                             | Reloads table               | None                      | Stored plain in YAML     | **CRITICAL** | ⚠️     |
| **Create Button**           | `<button type="submit">`      | `/api/webhooks`                | POST   | Session | Full webhook object          | `{"status": "ok"}`                                              | Form hidden, table reloaded | JSON error                | Atomic YAML write        | Medium       | ✅     |
| **Cancel Button**           | `<button>`                    | None (hides form)              | N/A    | N/A     | N/A                          | N/A                                                             | Form hidden, reset          | None                      | Client-side              | Low          | ✅     |
| **Table: Service Column**   | `<td>`                        | N/A (loaded via GET)           | N/A    | N/A     | N/A                          | N/A                                                             | Populated on load           | None                      | Display only             | Low          | ✅     |
| **Table: URL Column**       | `<td>` + copy icon            | None (clipboard)               | N/A    | N/A     | N/A                          | N/A                                                             | Tooltip shows full          | Clipboard error           | Truncated display        | Low          | ✅     |
| **Table: Secret Column**    | `<td>`                        | N/A (masked in GET)            | N/A    | N/A     | N/A                          | N/A                                                             | Shows `••••••`              | None                      | **Always masked in API** | Medium       | ✅     |
| **Test Button (per row)**   | `<button>`                    | `/api/webhooks/<service>/test` | POST   | Session | Empty body                   | `{"status": "ok", "status_code": 200, "response_time_ms": 123}` | Console logs result         | 404/504/502 handled       | Actual HTTP delivery     | **HIGH**     | ✅     |
| **Delete Button (per row)** | `<button>`                    | `/api/webhooks/<service>`      | DELETE | Session | None (service in URL)        | `{"status": "ok", "deleted": "..."}`                            | Confirmation, table reload  | 404 handled               | Atomic YAML write        | Medium       | ✅     |
| **Copy URL Icon**           | `<button>`                    | None (clipboard)               | N/A    | N/A     | N/A                          | N/A                                                             | Toast feedback              | Fallback to execCommand   | URL only, no secrets     | Low          | ✅     |

### **Integration Console Section**

| Element               | Type                       | Triggered Endpoint       | Method | Auth    | Real-Time Mechanism     | Buffer Strategy                        | Disconnect Handling      | Risk       | Status             |
| --------------------- | -------------------------- | ------------------------ | ------ | ------- | ----------------------- | -------------------------------------- | ------------------------ | ---------- | ------------------ | ------ | --- |
| **Console Header**    | `<h2>`                     | N/A                      | N/A    | N/A     | N/A                     | N/A                                    | N/A                      | Low        | ✅                 |
| **Console Div**       | `<div aria-live="polite">` | **NONE**                 | N/A    | N/A     | **❌ No SSE/WebSocket** | DOM-only (max 100 entries)             | **N/A (no connection)**  | **HIGH**   | ⚠️                 |
| **Send Sample Alert** | `<button>`                 | `/api/webhooks/test-all` | POST   | Session | Empty body              | `{"status": "ok", "results": [{...}]}` | Console logs each result | JSON error | Tests all webhooks | Medium | ✅  |

---

## **B. API-Level Issues**

### **✅ Fully Implemented Endpoints**

1. **`GET /api/webhooks`** — List all webhooks (secrets masked)
2. **`POST /api/webhooks`** — Create webhook with URL validation
3. **`DELETE /api/webhooks/<service_name>`** — Delete webhook (idempotent)
4. **`POST /api/developer/settings`** — Save Prometheus port + API key hash
5. **`POST /api/webhooks/test-all`** — Test all enabled webhooks
6. **`POST /api/webhooks/<service>/test`** — Test single webhook

### **⚠️ Schema & Validation Issues**

| Endpoint                           | Issue                                                       | Severity     | Fix Required                                                |
| ---------------------------------- | ----------------------------------------------------------- | ------------ | ----------------------------------------------------------- |
| **`POST /api/webhooks`**           | URL validation regex present but lenient                    | Medium       | ✅ Implemented (allows http/https, localhost, IP addresses) |
| **`POST /api/developer/settings`** | API key stored as SHA256 hash only — no retrieval mechanism | Medium       | ✅ Documented behavior (one-time display)                   |
| **`POST /api/webhooks`**           | Secrets stored **plain text** in `webhooks.yml`             | **CRITICAL** | ⚠️ **SECURITY RISK**                                        |

### **❌ Missing Endpoints**

| Missing Endpoint                                                               | Purpose                                   | Impact                                 | Priority |
| ------------------------------------------------------------------------------ | ----------------------------------------- | -------------------------------------- | -------- |
| **`GET /api/events/integration`** (SSE)                                        | Real-time event stream for console        | Console shows only local events        | **HIGH** |
| **`POST /api/webhooks/<service>/edit`** or **`PATCH /api/webhooks/<service>`** | Update existing webhook                   | Users must delete + recreate           | Medium   |
| **`GET /api/developer/settings`**                                              | Load current Prometheus port on page load | Port field always shows default `9100` | Medium   |

---

## **C. Real-Time Behavior Issues**

### **Integration Console**

| Feature                  | Current State                                            | Expected Behavior                           | Status      |
| ------------------------ | -------------------------------------------------------- | ------------------------------------------- | ----------- |
| **Event Stream**         | ❌ None                                                  | SSE connection to `/api/events/integration` | **BROKEN**  |
| **Webhook Test Results** | ✅ Shown (via `/api/webhooks/test-all` response parsing) | Real-time streaming                         | **PARTIAL** |
| **API Key Usage Logs**   | ❌ None                                                  | Live logs when API key is used              | **MISSING** |
| **Connection Status**    | ❌ None                                                  | Indicator showing SSE connection state      | **MISSING** |
| **Reconnection Logic**   | ❌ N/A                                                   | Exponential backoff retry on disconnect     | **MISSING** |
| **Event Buffering**      | ✅ Client-side (max 100 entries)                         | Server-side replay buffer for reconnects    | **PARTIAL** |

### **Webhooks Table**

| Action             | Refresh Behavior                        | Expected                      | Status              |
| ------------------ | --------------------------------------- | ----------------------------- | ------------------- |
| **Add Webhook**    | ✅ Calls `loadWebhooks()` after success | Automatic refresh             | ✅                  |
| **Delete Webhook** | ✅ Calls `loadWebhooks()` after success | Automatic refresh             | ✅                  |
| **Test Webhook**   | ✅ Logs to console immediately          | Console + table status update | **PARTIAL**         |
| **Edit Webhook**   | ❌ Not supported                        | N/A                           | **MISSING FEATURE** |

### **API Key**

| Action            | Behavior                                        | Expected                   | Status  |
| ----------------- | ----------------------------------------------- | -------------------------- | ------- |
| **Generate Key**  | ✅ Shown as masked (`••••••••••••••••`)         | One-time display           | ✅      |
| **Copy Key**      | ✅ Clears `pendingApiKey`, disables Copy button | Immediate wipe             | ✅      |
| **Save Settings** | ✅ Includes `api_key` in payload if present     | Backend hashes with SHA256 | ✅      |
| **Key Expiry**    | ❌ No expiry mechanism                          | N/A (stateless keys)       | **N/A** |

---

## **D. Security Findings**

### **🔴 Critical Issues**

1. **Webhook Secrets Stored Plain Text**

   - **Location:** `config/webhooks.yml`
   - **Risk:** File access = secret exposure
   - **Impact:** Attacker can impersonate TG Sentinel to downstream systems
   - **Recommendation:**

     ```python
     # Option 1: Encrypt secrets at rest with app key
     from cryptography.fernet import Fernet
     cipher = Fernet(app_key)
     webhook["secret"] = cipher.encrypt(secret.encode()).decode()

     # Option 2: Store hashed secrets (only works for signature verification)
     webhook["secret_hash"] = hashlib.sha256(secret.encode()).hexdigest()
     ```

   - **Fix Priority:** **IMMEDIATE**

2. **No API Key Revocation Mechanism**
   - **Risk:** Compromised keys cannot be invalidated
   - **Impact:** Leaked keys remain valid indefinitely
   - **Recommendation:** Implement key versioning + revocation list in Redis
   - **Fix Priority:** **HIGH**

### **🟡 High-Risk Issues**

3. **API Key Never Retrieved After Save**

   - **Current:** Frontend generates key, backend only stores SHA256 hash
   - **Risk:** Users lose key if they don't copy it
   - **Impact:** Must regenerate key (breaking downstream integrations)
   - **Status:** ✅ **Acceptable** (documented behavior: "Keys are only shown once")

4. **No Rate Limiting on Webhook Tests**

   - **Risk:** User can spam external services
   - **Impact:** IP bans from webhook providers
   - **Recommendation:** Limit to 5 tests/minute per service
   - **Fix Priority:** **MEDIUM**

5. **Webhook URL Validation Lenient**
   - **Current:** Accepts `http://` (not just `https://`)
   - **Risk:** Secrets sent over unencrypted connections
   - **Recommendation:** Warn or block non-HTTPS URLs (except localhost)
   - **Fix Priority:** **MEDIUM**

### **🟢 Medium-Risk Issues**

6. **No CSRF Tokens**

   - **Status:** Acceptable for API-only interface with session auth
   - **Recommendation:** Add CSRF if adding cookie-based UI interactions

7. **Copy to Clipboard Fallback Security**

   - **Current:** Uses `document.execCommand("copy")` as fallback
   - **Status:** ✅ Acceptable (deprecated but functional)
   - **Recommendation:** Show warning in console for old browsers

8. **Prometheus Port Not Loaded on Page Load**
   - **Risk:** User sees default `9100` even if custom port is saved
   - **Impact:** Confusion, potential misconfigurations
   - **Recommendation:** Add `GET /api/developer/settings` endpoint
   - **Fix Priority:** **LOW**

---

## **E. Recommended Improvements**

### **Backend Improvements**

#### **Priority 1: Security (Immediate)**

- [ ] **Encrypt webhook secrets at rest**

  ```python
  # Add to developer_routes.py
  from cryptography.fernet import Fernet

  def encrypt_secret(secret: str) -> str:
      cipher = Fernet(os.environ["WEBHOOK_SECRET_KEY"])
      return cipher.encrypt(secret.encode()).decode()

  def decrypt_secret(encrypted: str) -> str:
      cipher = Fernet(os.environ["WEBHOOK_SECRET_KEY"])
      return cipher.decrypt(encrypted.encode()).decode()
  ```

- [x] **API key revocation implemented** (Updated to use per-hash keys with individual TTLs)

  ```python
  @developer_bp.post("/api-keys/revoke")
  def api_key_revoke():
      # Creates individual Redis key per revoked hash with 30-day TTL
      key_hash = hashlib.sha256(api_key.encode()).hexdigest()
      revoked_key = f"tgsentinel:revoked_api_key:{key_hash}"
      redis_client.setex(revoked_key, 30 * 24 * 3600, "1")
      # Also maintains set for listing (optional)
      redis_client.sadd("tgsentinel:revoked_api_keys", key_hash)
      return jsonify({"status": "ok"})

  # Validation function for checking revoked keys
  def is_api_key_revoked(api_key: str) -> bool:
      key_hash = hashlib.sha256(api_key.encode()).hexdigest()
      return bool(redis_client.exists(f"tgsentinel:revoked_api_key:{key_hash}"))
  ```

#### **Priority 2: Real-Time Console (High)**

- [ ] **Implement SSE endpoint**

  ```python
  @developer_bp.get("/events/integration")
  def integration_events_stream():
      def event_stream():
          pubsub = redis_client.pubsub()
          pubsub.subscribe("tgsentinel:integration_events")
          for message in pubsub.listen():
              if message["type"] == "message":
                  yield f"data: {message['data']}\n\n"
      return Response(event_stream(), mimetype="text/event-stream")
  ```

- [ ] **Publish events from webhook test endpoints**
  ```python
  # In api_webhooks_test()
  redis_client.publish("tgsentinel:integration_events", json.dumps({
      "event": "webhook_test",
      "service": service_name,
      "status_code": response.status_code,
      "timestamp": datetime.utcnow().isoformat()
  }))
  ```

#### **Priority 3: Feature Completeness (Medium)**

- [ ] **Add webhook edit endpoint**

  ```python
  @developer_bp.patch("/webhooks/<service_name>")
  def api_webhooks_update(service_name: str):
      # Load existing, merge changes, atomic write
      pass
  ```

- [ ] **Add settings load endpoint**

  ```python
  @developer_bp.get("/developer/settings")
  def api_developer_settings_get():
      settings_path = _resolve_config_path("developer.yml")
      if settings_path.exists():
          with open(settings_path) as f:
              data = yaml.safe_load(f) or {}
          return jsonify({
              "prometheus_port": data.get("prometheus_port", 9100),
              "metrics_enabled": data.get("metrics_enabled", True)
          })
      return jsonify({"prometheus_port": 9100, "metrics_enabled": True})
  ```

- [ ] **Add rate limiting to webhook tests**

  ```python
  from flask_limiter import Limiter
  limiter = Limiter(key_func=lambda: request.remote_addr)

  @developer_bp.post("/webhooks/<service>/test")
  @limiter.limit("5/minute")
  def api_webhooks_test(service_name: str):
      # existing logic
  ```

### **Frontend Improvements**

#### **Priority 1: Real-Time Console (High)**

- [ ] **Connect to SSE endpoint**

  ```javascript
  const evtSource = new EventSource("/api/events/integration");
  evtSource.addEventListener("message", (e) => {
    const data = JSON.parse(e.data);
    appendDeveloperLog(`${data.event}: ${JSON.stringify(data)}`);
  });
  evtSource.onerror = () => {
    appendDeveloperLog("ERROR: Real-time connection lost. Retrying...");
  };
  ```

- [ ] **Add connection status indicator**
  ```html
  <span id="console-status" class="badge bg-success">Connected</span>
  ```

#### **Priority 2: UX Polish (Medium)**

- [ ] **Load Prometheus port on page load**

  ```javascript
  async function loadDeveloperSettings() {
    const response = await fetch("/api/developer/settings");
    const data = await response.json();
    document.getElementById("prometheus-port").value = data.prometheus_port;
  }
  document.addEventListener("DOMContentLoaded", loadDeveloperSettings);
  ```

- [ ] **Add webhook edit modal**

  ```html
  <button
    class="btn btn-outline-primary btn-sm"
    onclick="editWebhook('service1')"
  >
    Edit
  </button>
  ```

- [ ] **Show HTTPS warning for non-secure URLs**
  ```javascript
  if (!url.startsWith("https://") && !url.includes("localhost")) {
    showToast("Warning: Sending secrets over HTTP is insecure", "warning");
  }
  ```

#### **Priority 3: Observability (Low)**

- [ ] **Add webhook test history**
  ```javascript
  const testHistory = JSON.parse(
    localStorage.getItem("webhook_test_history") || "[]"
  );
  testHistory.push({ service, status, timestamp: Date.now() });
  localStorage.setItem(
    "webhook_test_history",
    JSON.stringify(testHistory.slice(-50))
  );
  ```

### **API Design Improvements**

- [ ] **Consistent error envelope**

  ```json
  {
    "status": "error",
    "code": "INVALID_URL_FORMAT",
    "message": "Webhook URL must start with https://",
    "field": "url"
  }
  ```

- [ ] **Pagination for webhooks**
  ```python
  @developer_bp.get("/webhooks")
  def api_webhooks_list():
      page = request.args.get("page", 1, type=int)
      per_page = request.args.get("per_page", 20, type=int)
      # pagination logic
  ```

### **Observability Improvements**

- [ ] **Structured logging for webhook tests**

  ```python
  logger.info("Webhook test", extra={
      "handler": "WEBHOOKS",
      "service": service_name,
      "url": url,
      "status_code": response.status_code,
      "response_time_ms": elapsed_ms
  })
  ```

- [ ] **Metrics endpoint for webhook stats**
  ```python
  @developer_bp.get("/webhooks/stats")
  def api_webhooks_stats():
      return jsonify({
          "total": len(webhooks),
          "enabled": sum(1 for w in webhooks if w.get("enabled")),
          "last_tested": redis_client.get("tgsentinel:webhooks:last_tested")
      })
  ```

---

## **F. Testing Status**

### **✅ Covered by Tests**

- Webhook creation (valid/invalid URLs)
- Webhook deletion (found/not found)
- Webhook duplicate detection (409 conflict)
- Webhook URL validation (http/https/localhost/IP)
- Webhook test delivery (success/timeout)
- Webhook test with secret (HMAC signature)
- Test-all endpoint (multiple services, skipped disabled)
- Developer settings save (port validation)

### **⚠️ Missing Test Coverage**

- API key generation (crypto-safety)
- API key copy behavior (clipboard security)
- SSE connection lifecycle
- Webhook edit operations (not implemented)
- Rate limiting on webhook tests
- Secret encryption/decryption
- Settings load endpoint (not implemented)

---

## **G. Risk Summary**

### **Critical Risks (Immediate Action Required)**

1. **Webhook secrets stored plain text** → Encrypt at rest
2. **No API key revocation** → Implement key versioning + revocation list

### **High Risks (Fix Within Sprint)**

3. **No real-time console** → Implement SSE endpoint
4. **No rate limiting on webhook tests** → Add Flask-Limiter
5. **Lenient URL validation** → Warn on non-HTTPS

### **Medium Risks (Plan for Next Release)**

6. **No webhook edit functionality** → Add PATCH endpoint
7. **Port not loaded on page load** → Add GET settings endpoint
8. **No webhook test history** → Add persistence layer

### **Low Risks (Backlog)**

9. **No pagination** → Add when webhook count > 50
10. **No CSRF tokens** → Add if UI becomes cookie-based

---

## **H. Compliance Verdict**

| Category                | Score | Status                         |
| ----------------------- | ----- | ------------------------------ |
| **Element Enumeration** | 100%  | ✅ All elements documented     |
| **API Binding**         | 85%   | ⚠️ SSE missing, edit missing   |
| **Real-Time Behavior**  | 40%   | ❌ Console not real-time       |
| **Validation Logic**    | 90%   | ✅ Strong validation           |
| **Error Handling**      | 95%   | ✅ Comprehensive               |
| **Security**            | 60%   | 🔴 Plain text secrets critical |
| **UX Consistency**      | 85%   | ✅ Good patterns               |
| **Test Coverage**       | 75%   | ⚠️ Missing SSE/edit tests      |

### **Overall Grade: B- (Functional but Insecure)**

**Blockers for Production:**

1. Encrypt webhook secrets at rest
2. Implement API key revocation
3. Add real-time console (SSE)

**Recommendation:** Address critical security issues before enabling webhooks in production. Real-time console is highly recommended for operational visibility.

---

## **I. Implementation Checklist**

### **Sprint 1: Security Hardening (1-2 days)**

- [x] Add `WEBHOOK_SECRET_KEY` to environment variables (Required - application fails without it)
- [x] Implement `encrypt_secret()` / `decrypt_secret()` helpers (Implemented with fail-fast validation)
- [ ] Migrate existing webhooks to encrypted format
- [x] Add API key revocation endpoint + Redis set (Implemented with per-hash keys)
- [ ] Update tests for encrypted secrets

**Note**: `WEBHOOK_SECRET_KEY` is now a **required** environment variable. The application will refuse to start if it's not set, preventing data corruption from auto-generated keys. Generate a key with:

```bash
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

### **Sprint 2: Real-Time Console (2-3 days)**

- [ ] Implement SSE endpoint `/api/events/integration`
- [ ] Add Redis pub/sub for integration events
- [ ] Update frontend to connect via `EventSource`
- [ ] Add connection status indicator
- [ ] Add reconnection logic with exponential backoff
- [ ] Test SSE connection lifecycle

### **Sprint 3: Feature Completeness (2-3 days)**

- [ ] Add webhook edit (PATCH) endpoint
- [ ] Add settings load (GET) endpoint
- [ ] Implement rate limiting on webhook tests
- [ ] Add HTTPS warning for insecure URLs
- [ ] Update UI to load Prometheus port
- [ ] Add webhook edit modal

### **Sprint 4: Polish & Observability (1-2 days)**

- [ ] Add structured logging for webhook operations
- [ ] Add webhook stats endpoint
- [ ] Add test history to console
- [ ] Improve error messages with field-level details
- [ ] Add integration tests for SSE
- [ ] Update documentation

---

**End of Audit Report**
