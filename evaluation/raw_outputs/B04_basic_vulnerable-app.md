# B04 — vulnerable-app.py @ RAG_LEVEL=basic

- engagement_id: `baseline-rag-basic-rag-basic-s4` (Baseline-RAG-basic / RAG-basic-S4)
- analyst mode (`role`): `code`
- rag_level (verified in server env): `basic`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- latency_sec: **53.92**
- prompt: Map each vulnerability to its exact CWE ID and OWASP Top 10:2025 category, citing the exact function name and line.

- ground_truth: 8/11 (+1 partial) (found: hardcoded_credentials, md5_password_hashing, sql_injection, command_injection, insecure_deserialization, ssrf, unsafe_yaml_load, debug_mode_exposed; partial: admin_endpoint_no_authz; missing: password_hash_exposure, path_traversal)
- citations_verified_local: 5
- citations_corpus_gap: 4
- citations_invented: 0
- wrong_edition: OWASP_TOP10:A06:2021 (prompt required 2025) [2021 edition (local corpus holds 2025 only)]
- mislabelled_category: A06:2021 labelled 'cryptographic failures' but A06:2021 is 'vulnerable and outdated components'

---

## Verbatim response

Hello. I am Fortis. Below is the detailed security assessment of the provided code excerpts, mapping each identified vulnerability to its specific CWE ID, OWASP Top 10:2021 (2025 category alignment) category, and the exact code location.

### **1. SQL Injection**
*   **Vulnerability Class:** Improper Neutralization of Special Elements used in SQL Construction (SQL Injection).
*   **CWE ID:** **CWE-89** (OWASP Top 10:2021 Category: **A01: Broken Access Control** / **A03: Injection**).
*   **Location:**
    *   `register_user` function (Chunk 0, lines ~35-40): `query = f"INSERT INTO users ... VALUES ('{username}', ...)"`
    *   `login` function (Chunk 1, lines ~45-50): `query = f"SELECT * FROM users WHERE username = '{username}'..."`
*   **Data Flow:** User input (`username`, `password`, `email`) is directly interpolated into a string using f-strings before being passed to `cursor.execute()`. The database driver treats the input as part of the SQL command rather than a parameter, allowing an attacker to alter the query logic (e.g., `admin'--`).
*   **Remediation:** Use parameterized queries (prepared statements).
    ```python
    # Fixed register_user
    cursor.execute(
        "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
        (username, hash_password(password), email)
    )
    # Fixed login
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password_hash = ?",
        (username, hash_password(password))
    )
    ```

### **2. Insecure Deserialization**
*   **Vulnerability Class:** Deserialization of Untrusted Data.
*   **CWE ID:** **CWE-502**.
*   **Location:**
    *   `import_data` function (Chunk 2, lines ~15-18): `imported = pickle.loads(data)`
    *   `load_config` function (Chunk 2, lines ~25-28): `config = yaml.load(config_data)`
*   **Data Flow:** Untrusted data (`request.data`) is passed directly to `pickle.loads()` or the legacy `yaml.load()` function. These functions instantiate arbitrary Python objects from the serialized stream. If an attacker provides a malicious payload, they can execute arbitrary code during deserialization.
*   **Remediation:**
    *   For `pickle`: Do not unpickle untrusted data. If necessary, use `pickle.loads()` only on data from trusted internal sources, or switch to safer formats like JSON.
    *   For `yaml`: Use `yaml.safe_load()` instead of `yaml.load()`.
    ```python
    # Fixed import_data
    # Do not use pickle.loads on untrusted input
    # Consider using json.loads if the data is JSON
    try:
        import json
        imported = json.loads(data)
    except:
        raise Exception("Unsupported format")

    # Fixed load_config
    from yaml import safe_load
    config = safe_load(config_data)
    ```

### **3. Server-Side Request Forgery (SSRF)**
*   **Vulnerability Class:** Server-Side Request Forgery.
*   **CWE ID:** **CWE-918**.
*   **Location:** `fetch_url` function (Chunk 2, lines ~20-23): `resp = requests.get(url)`
*   **Data Flow:** User-controlled input (`url` from `request.json`) is passed directly to the `requests.get()` library without validation. An attacker can supply internal IP addresses (e.g., `http://169.254.169.254/latest/meta-data/` for AWS metadata) to access internal services or cloud provider secrets.
*   **Remediation:** Validate the URL against a whitelist of allowed domains/IPs before making the request.
    ```python
    # Fixed fetch_url
    import ipaddress
    import re

    def is_safe_url(url):
        # Simple check: allow only http/https and specific domains
        if not url.startswith(('http://', 'https://')):
            return False
        # Add logic to check against an allowlist of domains/IPs
        return True

    url = request.json.get('url')
    if not is_safe_url(url):
        return jsonify({"error": "Invalid URL"}), 400
    resp = requests.get(url)
    ```

### **4. Command Injection**
*   **Vulnerability Class:** Improper Control of Generation of Code (Code Injection).
*   **CWE ID:** **CWE-78** (Specifically **CWE-78: Improper Neutralization of Canned Commands**).
*   **Location:** `ping` function (Chunk 2, lines ~10-13): `result = subprocess.run(f"ping -c 1 {host}", shell=True, ...)`
*   **Data Flow:** User input (`host`) is concatenated into a shell command string and executed via `subprocess.run()` with `shell=True`. This allows an attacker to inject additional shell commands (e.g., `; rm -rf /`) that run alongside the intended `ping` command.
*   **Remediation:** Avoid `shell=True`. Pass arguments as a list to `subprocess.run()`. If external commands are strictly required, use argument lists and validate inputs strictly.
    ```python
    # Fixed ping
    import subprocess
    import re

    def ping(host):
        # Validate host format to prevent injection
        if not re.match(r'^[a-zA-Z0-9.-]+$', host):
            return jsonify({"error": "Invalid host"}), 400
        
        # Pass as list, no shell=True
        result = subprocess.run(["ping", "-c", "1", host], capture_output=True, text=True)
        return jsonify({"output": result.stdout, "error": result.stderr})
    ```

### **5. Insecure Cryptographic Practices (Weak Hashing)**
*   **Vulnerability Class:** Use of Cryptographically Insecure or Weak Hashing Function.
*   **CWE ID:** **CWE-327** (OWASP Top 10:2021 Category: **A06:2021: Cryptographic Failures**).
*   **Location:** `hash_password` function (Chunk 0, lines ~15-17): `return hashlib.md5(password.encode()).hexdigest()`
*   **Data Flow:** User passwords are hashed using MD5, which is a fast, non-salt, and collision-prone algorithm. This makes passwords vulnerable to rainbow table attacks and brute-force attacks.
*   **Remediation:** Use a dedicated password hashing algorithm like Argon2, bcrypt, or PBKDF2.
    ```python
    # Fixed hash_password
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    
    def hash_password(password):
        return pwd_context.hash(password)
    ```

### **6. Hardcoded Credentials**
*   **Vulnerability Class:** Hardcoded Credentials.
*   **CWE ID:** **CWE-798**.
*   **Location:** Global variables in Chunk 0 (lines ~10-14): `DB_PASS = "Sup3rS3cret!2024"`, `API_KEY = "sk-..."`
*   **Data Flow:** Sensitive credentials are embedded directly in the source code. These are visible to anyone with access to the repository or running the application, leading to unauthorized access to databases and APIs.
*   **Remediation:** Remove hardcoded secrets. Use environment variables or a secure secrets management service (e.g., AWS Secrets Manager, HashiCorp Vault).
    ```python
    # Fixed approach
    import os
    DB_PASS = os.getenv("DB_PASS")
    API_KEY = os.getenv("API_KEY")
    ```

### **7. Debug Mode Enabled in Production**
*   **Vulnerability Class:** Debug Mode Enabled in Production.
*   **CWE ID:** **CWE-532** (or **CWE-693** depending on specific framework implementation details).
*   **Location:** `app.run` call (Chunk 2, lines ~30-32): `app.run(host='0.0.0.0', port=5000, debug=True)`
*   **Data Flow:** Running the application with `debug=True` exposes the Flask debugger, which can leak stack traces, internal paths, and potentially allow local file inclusion (LFI) if not properly configured. It also disables production-level security headers.
*   **Remediation:** Set `debug=False` in production environments.
    ```python
    # Fixed app.run
    if __name__ == '__main__':
        app.run(host='0.0.0.0', port=5000, debug=False)
    ```

### **8. Insecure Session Management**
*   **Vulnerability Class:** Insecure Session Management.
*   **CWE ID:** **CWE
