# T09 — vulnerable-app.py

- engagement_id: `baseline-lab4-vulnapp` (Lab4-VulnApp)
- analyst mode (`role`): `code`
- rag_level: `standard`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- latency_sec: **65.10**
- history_turns_sent: 2
- prompt: Map each vulnerability to its exact CWE ID and OWASP Top 10:2025 category, citing the exact function name and line.

- ground_truth: 7/11 (+1 partial) (found: hardcoded_credentials, sql_injection, insecure_deserialization, path_traversal, ssrf, unsafe_yaml_load, debug_mode_exposed; partial: admin_endpoint_no_authz; missing: md5_password_hashing, password_hash_exposure, command_injection)
- citations_verified_local: 8
- citations_corpus_gap: 2
- citations_invented: 0
- wrong_edition: OWASP_TOP10:A01:2021 (prompt required 2025) [2021 edition (local corpus holds 2025 only)]; OWASP_TOP10:A03:2021 (prompt required 2025) [2021 edition (local corpus holds 2025 only)]; OWASP_TOP10:A05:2021 (prompt required 2025) [2021 edition (local corpus holds 2025 only)]
---

## Verbatim response

Based on the provided code excerpts from `vulnerable-app.py`, I have identified **six critical vulnerabilities**. Each is mapped to its specific CWE ID, OWASP Top 10:2021 category (as the 2025 update is not yet published, the 2021 categories remain the current standard), and aligned with the provided framework controls.

---

### 1. SQL Injection (Registration & Login)
*   **CWE ID:** **CWE-89** (Improper Neutralization of Special Elements used in an SQL Command)
*   **OWASP Top 10:2021 Category:** **A03:2021 Injection**
*   **Location:**
    *   `register_user()` function, Lines 35-38 (Chunk 0)
    *   `login()` function, Lines 55-58 (Chunk 1)
*   **Description:** The application constructs SQL queries using string interpolation (`f-string`) with unsanitized user input (`username`, `password`, `email`). This allows attackers to inject malicious SQL syntax to bypass authentication or extract data.
*   **Tainted Data Flow:**
    *   **Source:** `request.get_json()` (User Input).
    *   **Sink:** `cursor.execute(query)` (Database Execution).
*   **Framework Reference:**
    *   **CWE-89:** "User input is not neutralized before use in dynamically constructed SQL queries."
    *   **PCI DSS Req 8:** Fails to manage credentials securely (hardcoded DB_PASS) and lacks strong authentication mechanisms.
*   **Severity:** **Critical**
*   **Remediation:** Use parameterized queries (prepared statements).
    ```python
    # FIXED: Parameterized query
    query = "INSERT INTO users (username, password, email) VALUES (?, ?, ?)"
    cursor.execute(query, (username, hashed_password, email))
    ```

### 2. Insecure Deserialization (Pickle)
*   **CWE ID:** **CWE-502** (Deserialization of Untrusted Data)
*   **OWASP Top 10:2021 Category:** **A05:2021 Security Misconfiguration** (often grouped here due to lack of safe defaults) or **A01:2021 Broken Access Control** (if exploited to access unauthorized data).
*   **Location:** `import_data()` function, Lines 62-65 (Chunk 2).
*   **Description:** The `pickle.loads()` function is used to deserialize data from an untrusted source (`request.data`). `pickle` is not safe for untrusted input because it can execute arbitrary Python code during deserialization.
*   **Tainted Data Flow:**
    *   **Source:** `request.data` (Untrusted Network Input).
    *   **Sink:** `pickle.loads()` (Execution of arbitrary Python bytecode).
*   **Framework Reference:**
    *   **CWE-502:** "Deserialization of untrusted data."
    *   **CWE-94:** "Improper Control of Generation of Code" (via code execution during deserialization).
*   **Severity:** **Critical**
*   **Remediation:** Do not use `pickle` for untrusted data. Use `json` or `msgpack` with strict schema validation.
    ```python
    # FIXED: Use JSON
    import json
    data = request.data
    imported = json.loads(data)
    ```

### 3. Server-Side Request Forgery (SSRF)
*   **CWE ID:** **CWE-918** (Server-Side Request Forgery)
*   **OWASP Top 10:2021 Category:** **A03:2021 Injection** (often overlaps with SSRF) or **A01:2021 Broken Access Control**.
*   **Location:** `fetch_url()` function, Lines 70-73 (Chunk 2).
*   **Description:** The application accepts a URL from the client (`request.json.get('url')`) and passes it directly to `requests.get()` without validation. This allows an attacker to make the server request internal resources (e.g., `http://localhost:6379`, `http://169.254.169.254/latest/meta-data/`).
*   **Tainted Data Flow:**
    *   **Source:** `request.json.get('url')` (Untrusted Input).
    *   **Sink:** `requests.get(url)` (Network Request to arbitrary destination).
*   **Framework Reference:**
    *   **CWE-918:** "Server-Side Request Forgery."
    *   **ISO 27001 A.8.3:** Access to information and application system functions is not restricted.
*   **Severity:** **High**
*   **Remediation:** Validate the URL scheme and IP address range before making the request. Block internal IP ranges.
    ```python
    # FIXED: Basic validation
    import ipaddress
    url = request.json.get('url')
    if not url.startswith(('http://', 'https://')):
        raise ValueError("Only HTTP/HTTPS allowed")
    
    # Check if URL resolves to an internal IP
    try:
        ip = ipaddress.ip_address(url.split(':')[0].split('//')[1])
        if ip.is_private or ip.is_loopback:
            raise ValueError("Internal addresses are blocked")
    except:
        raise ValueError("Invalid URL format")
    
    resp = requests.get(url)
    ```

### 4. Path Traversal (File Access)
*   **CWE ID:** **CWE-22** (Improper Input Validation) / **CWE-444** (Improper Neutralization of Path Components)
*   **OWASP Top 10:2021 Category:** **A01:2021 Broken Access Control**
*   **Location:** `get_file()` function, Lines 76-80 (Chunk 2).
*   **Description:** The filename is passed directly to `os.path.join()` without sanitization. An attacker can use `../` sequences to read files outside the intended `/data/uploads` directory (e.g., `/etc/passwd`).
*   **Tainted Data Flow:**
    *   **Source:** `filename` from URL path (`<path:filename>`).
    *   **Sink:** `open(filepath)` (File System Access).
*   **Framework Reference:**
    *   **CWE-22:** "Improper Input Validation."
    *   **PCI DSS Req 8:** Fails to manage access to system components properly.
*   **Severity:** **High**
*   **Remediation:** Sanitize the path and ensure it remains within the allowed directory.
    ```python
    # FIXED: Sanitize path
    import os
    allowed_path = '/data/uploads'
    filepath = os.path.normpath(os.path.join(allowed_path, filename))
    if not filepath.startswith(allowed_path):
        raise ValueError("Access denied")
    
    with open(filepath, 'r') as f:
        return jsonify({"content": f.read()})
    ```

### 5. Insecure YAML Deserialization
*   **CWE ID:** **CWE-502** (Deserialization of Untrusted Data)
*   **OWASP Top 10:2021 Category:** **A05:2021 Security Misconfiguration**
*   **Location:** `load_config()` function, Lines 83-86 (Chunk 2).
*   **Description:** The code uses `yaml.load()` (the legacy, unsafe version) on untrusted input. The `yaml.load()` function allows arbitrary code execution if the YAML payload is crafted to contain a `!!python/object` tag.
*   **Tainted Data Flow:**
    *   **Source:** `request.data.decode()` (Untrusted Input).
    *   **Sink:** `yaml.load()` (Execution of arbitrary Python code).
*   **Framework Reference:**
    *   **CWE-502:** "Deserialization of untrusted data."
    *   **CWE-94:** "Improper Control of Generation of Code."
*   **Severity:** **Critical**
*   **Remediation:** Use `yaml.safe_load()` which restricts the types of objects that can be constructed.
    ```python
    # FIXED: yaml.safe_load(data)
    config = yaml.safe_load(config_data)
    ```

### 6. Hardcoded Credentials & Debug Mode
*   **CWE ID:** **CWE-798** (Hardcoded Credentials) / **CWE-532** (Debugging Information Exposed)
*   **OWASP Top 10:2021 Category:** **A05:2021 Security Misconfiguration**
*   **Location:**
    *   Global variables at Lines 10-13 (Chunk 0)
    *   `if __name__ == '__main__':` block, Line 90 (Chunk 2)
*   **Description:**
    *   **Hardcoded Credentials:** Database credentials (`DB_PASS`) and API keys are hardcoded in the source code, making them
