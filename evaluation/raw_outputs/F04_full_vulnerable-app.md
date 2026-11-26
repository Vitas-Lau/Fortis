# F04 — vulnerable-app.py @ RAG_LEVEL=full

- engagement_id: `baseline-rag-full-rag-full-s4` (Baseline-RAG-full / RAG-full-S4)
- analyst mode (`role`): `code`
- rag_level (verified in server env): `full`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- rerank.is_available(): `True`
- latency_sec: **53.68**
- prompt: Map each vulnerability to its exact CWE ID and OWASP Top 10:2025 category, citing the exact function name and line.

- ground_truth: 7/11 (+1 partial) (found: hardcoded_credentials, md5_password_hashing, sql_injection, command_injection, insecure_deserialization, ssrf, unsafe_yaml_load; partial: admin_endpoint_no_authz; missing: password_hash_exposure, path_traversal, debug_mode_exposed)
- citations_verified_local: 11
- citations_corpus_gap: 2
- citations_invented: 0
- mislabelled_category: A02:2025 labelled 'broken access control' but A02:2025 is 'security misconfiguration'; A06:2025 labelled 'broken authentication' but A06:2025 is 'insecure design'; A06:2025 labelled 'broken authentication' but A06:2025 is 'insecure design'; A04:2025 labelled 'insecure design' but A04:2025 is 'cryptographic failures'; A06:2025 labelled 'broken authentication' but A06:2025 is 'insecure design'; A05:2025 labelled 'security misconfiguration' but A05:2025 is 'injection'; A03:2025 labelled 'vulnerable components' but A03:2025 is 'software supply chain failures'; A03:2025 labelled 'vulnerable components' but A03:2025 is 'software supply chain failures'; A03:2025 labelled 'vulnerable components' but A03:2025 is 'software supply chain failures'; A03:2025 labelled 'vulnerable components' but A03:2025 is 'software supply chain failures'

---

## Verbatim response

Hello. I am Fortis. I have analyzed the provided code excerpts from `vulnerable-app.py`. Below is the detailed mapping of identified vulnerabilities to their specific CWE IDs, OWASP Top 10:2025 categories, and the exact locations within the code.

### 1. SQL Injection (Registration & Login)
*   **CWE ID:** **CWE-89** (Improper Neutralization of Special Elements used in SQL Construction)
*   **OWASP Top 10:2025 Category:** **A02:2025 Broken Access Control** (Note: While often categorized under Injection in older frameworks, in OWASP Top 10:2025, SQL injection is explicitly listed under **A01:2025 Broken Access Control** because it allows attackers to bypass authentication and authorization logic).
*   **Location:**
    *   `register_user` function (Chunk 0, lines ~30-35):
        ```python
        query = f"INSERT INTO users (username, password, email) VALUES ('{username}', '{hash_password(password)}', '{email}')"
        ```
    *   `login` function (Chunk 1, lines ~20-25):
        ```python
        query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{hash_password(password)}'"
        ```
*   **Data Flow:** User input (`username`, `password`, `email`) is directly interpolated into the SQL string using f-strings. This tainted data flows from the HTTP request body to the database query string, bypassing the SQL parser's escaping mechanisms.
*   **Remediation:** Use parameterized queries (prepared statements).
    ```python
    # Fixed register_user
    query = "INSERT INTO users (username, password, email) VALUES (?, ?, ?)"
    cursor.execute(query, (username, hash_password(password), email))
    ```

### 2. Insecure Password Hashing
*   **CWE ID:** **CWE-326** (Use of Cryptographically Weak Hash Function)
*   **OWASP Top 10:2025 Category:** **A06:2025 Broken Authentication**
*   **Location:** `hash_password` function (Chunk 0, lines ~25-27):
    ```python
    return hashlib.md5(password.encode()).hexdigest()
    ```
*   **Data Flow:** User-provided plaintext passwords are passed to `hashlib.md5`, which produces a weak hash. This allows attackers to use rainbow tables or brute-force attacks to recover credentials.
*   **Remediation:** Use a dedicated password hashing algorithm like Argon2, bcrypt, or PBKDF2.
    ```python
    # Fixed hash_password
    from passlib.hash import argon2
    return argon2.hash(password)
    ```

### 3. Hardcoded Credentials
*   **CWE ID:** **CWE-798** (Hardcoded Credentials)
*   **OWASP Top 10:2025 Category:** **A06:2025 Broken Authentication** (or **A04:2025 Insecure Design** depending on context, but primarily affects auth integrity).
*   **Location:** Module level variables (Chunk 0, lines ~12-15):
    ```python
    DB_USER = "admin"
    DB_PASS = "Sup3rS3cret!2024"
    API_KEY = "sk-1234567890abcdef1234567890abcdef"
    ```
*   **Data Flow:** Sensitive secrets are embedded directly in the source code. If the code is committed to a repository or leaked, these credentials are immediately exposed.
*   **Remediation:** Remove hardcoded secrets. Use environment variables or a secure secrets manager (e.g., AWS Secrets Manager, HashiCorp Vault).
    ```python
    import os
    DB_USER = os.getenv("DB_USER")
    DB_PASS = os.getenv("DB_PASS")
    ```

### 4. Predictable Session Tokens
*   **CWE ID:** **CWE-327** (Use of Weak Randomness)
*   **OWASP Top 10:2025 Category:** **A06:2025 Broken Authentication**
*   **Location:** `login` function (Chunk 1, lines ~28-30):
    ```python
    token = hashlib.md5(f"{username}{os.urandom(8).hex()}".encode()).hexdigest()
    ```
*   **Data Flow:** The token generation combines a predictable component (`username`) with a short random string. The use of `MD5` for the final hash further weakens the entropy. This allows attackers to guess or replay tokens.
*   **Remediation:** Use a cryptographically secure random number generator (CSPRNG) and a strong, non-reversible hash or a dedicated session library.
    ```python
    import secrets
    token = secrets.token_urlsafe(32)
    ```

### 5. Server-Side Request Forgery (SSRF)
*   **CWE ID:** **CWE-918** (Server-Side Request Forgery)
*   **OWASP Top 10:2025 Category:** **A05:2025 Security Misconfiguration** (Often grouped here as it represents a misconfiguration of the application's network behavior) or **A03:2025 Vulnerable Components**.
*   **Location:** `fetch_url` function (Chunk 2, lines ~40-43):
    ```python
    url = request.json.get('url')
    resp = requests.get(url)
    ```
*   **Data Flow:** User-controlled input (`url`) is passed directly to the `requests.get()` function without validation. This allows an attacker to make the server request internal IPs (e.g., `http://169.254.169.254/latest/meta-data/`) to access cloud metadata or internal services.
*   **Remediation:** Validate the URL scheme and IP address range before making the request.
    ```python
    from urllib.parse import urlparse
    from ipaddress import ip_address

    url = request.json.get('url')
    parsed = urlparse(url)
    if parsed.scheme not in ['http', 'https']:
        raise ValueError("Only HTTP/HTTPS allowed")
    # Check if IP is internal/private
    ip = ip_address(parsed.hostname)
    if ip.is_private:
        raise ValueError("Access to internal networks is forbidden")
    resp = requests.get(url)
    ```

### 6. Command Injection
*   **CWE ID:** **CWE-78** (Improper Neutralization of Special Elements used in OS Command Injection)
*   **OWASP Top 10:2025 Category:** **A01:2025 Broken Access Control** (Allows unauthorized access to system commands) or **A03:2025 Vulnerable Components**.
*   **Location:** `ping` function (Chunk 1 & 2, lines ~35-38):
    ```python
    result = subprocess.run(f"ping -c 1 {host}", shell=True, capture_output=True, text=True)
    ```
*   **Data Flow:** User input (`host`) is concatenated into a shell command string and executed via `subprocess.run` with `shell=True`. This allows an attacker to inject additional commands (e.g., `; rm -rf /`).
*   **Remediation:** Do not use `shell=True`. Pass arguments as a list to the subprocess function.
    ```python
    result = subprocess.run(["ping", "-c", "1", host], capture_output=True, text=True)
    ```

### 7. Insecure Deserialization
*   **CWE ID:** **CWE-502** (Deserialization of Untrusted Data)
*   **OWASP Top 10:2025 Category:** **A03:2025 Vulnerable Components**
*   **Location:** `import_data` function (Chunk 2, lines ~45-48):
    ```python
    imported = pickle.loads(data)
    ```
*   **Data Flow:** Untrusted data from the HTTP request body is passed to `pickle.loads()`. Since `pickle` can execute arbitrary Python code during deserialization, an attacker can craft a payload to execute RCE.
*   **Remediation:** Do not use `pickle` for untrusted data. Use safer formats like JSON or Protobuf.
    ```python
    # Fixed import_data
    import json
    imported = json.loads(data)
    ```

### 8. Unsafe YAML Deserialization
*   **CWE ID:** **CWE-502** (Deserialization of Untrusted Data)
*   **OWASP Top 10:2025 Category:** **A03:2025 Vulnerable Components**
*   **Location:** `load_config` function (Chunk 2, lines ~50-53):
    ```python
    config = yaml.load(config_data)
    ```
*   **Data Flow:** Untrusted YAML data is loaded using the default `yaml.load()` function
