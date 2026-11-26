# T08 — vulnerable-app.py

- engagement_id: `baseline-lab4-vulnapp` (Lab4-VulnApp)
- analyst mode (`role`): `code`
- rag_level: `standard`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- latency_sec: **55.33**
- history_turns_sent: 0
- prompt: What vulnerabilities are in this application?

- ground_truth: 6/11 (found: sql_injection, command_injection, insecure_deserialization, path_traversal, ssrf, unsafe_yaml_load; partial: none; missing: hardcoded_credentials, md5_password_hashing, admin_endpoint_no_authz, password_hash_exposure, debug_mode_exposed)
- citations_verified_local: 9
- citations_corpus_gap: 0
- citations_invented: 0
---

## Verbatim response

Based on the provided code excerpts from `vulnerable-app.py`, I have identified **seven distinct critical and high-severity vulnerabilities**. These issues expose the application to Remote Code Execution (RCE), data exfiltration, and credential theft.

Below is the detailed assessment, mapped to specific CWE IDs, tainted data flows, and remediation strategies aligned with the provided frameworks.

---

### 1. SQL Injection (Registration & Login)
*   **Location:** `vulnerable-app.py` (Chunk 0 & Chunk 1), Lines 35-38 (Registration) and Lines 55-58 (Login).
*   **CWE:** **CWE-89 (Improper Neutralization of Special Elements used in an OS Command String)** / **CWE-89 (SQL Injection)**.
*   **Description:** The application constructs SQL queries using string interpolation (`f-string`) with user-supplied input (`username`, `password`, `email`). This allows an attacker to inject malicious SQL syntax.
*   **Tainted Data Flow:**
    *   **Source:** `request.get_json()` (User Input).
    *   **Taint Propagation:** Passed directly into the `f-string` query construction.
    *   **Sink:** `cursor.execute(query)` (Database Execution).
*   **Framework Reference:**
    *   **MITRE ATT&CK T1203:** Exploits software vulnerabilities in client applications to execute code (via SQL injection).
    *   **ISO 27001 A.8.8:** Technical vulnerabilities are not being remediated in a timely manner.
*   **Severity:** **Critical**
*   **Remediation:** Use parameterized queries (prepared statements) to separate logic from data.
    ```python
    # VULNERABLE: f-string interpolation
    query = f"INSERT INTO users ... VALUES ('{username}', ...)"

    # FIXED: Parameterized query
    query = "INSERT INTO users (username, password, email) VALUES (?, ?, ?)"
    cursor.execute(query, (username, hashed_password, email))
    ```

### 2. Command Injection (Ping Endpoint)
*   **Location:** `vulnerable-app.py` (Chunk 0 & Chunk 1), Lines 43-46 (Ping function).
*   **CWE:** **CWE-78 (Improper Neutralization of Special Elements used in an OS Command String)**.
*   **Description:** The `subprocess.run()` function is called with `shell=True` and a user-controlled variable (`host`) concatenated directly into the command string. An attacker can inject shell metacharacters (e.g., `; rm -rf /`) to execute arbitrary commands on the server.
*   **Tainted Data Flow:**
    *   **Source:** `request.args.get('host')` (User Input).
    *   **Taint Propagation:** Passed as an argument to the shell command string.
    *   **Sink:** `subprocess.run()` (OS Command Execution).
*   **Framework Reference:**
    *   **MITRE ATT&CK TA0002:** Adversaries run malicious code via command and scripting interpreters.
    *   **MITRE ATT&CK T1053:** Abuse of task scheduling or command execution.
*   **Severity:** **Critical**
*   **Remediation:** Remove `shell=True` and use argument lists. If arguments must be passed, ensure they are validated against a whitelist of allowed hosts.
    ```python
    # VULNERABLE: shell=True with string concatenation
    result = subprocess.run(f"ping -c 1 {host}", shell=True, ...)

    # FIXED: Argument list (no shell=True)
    result = subprocess.run(["ping", "-c", "1", host], capture_output=True, text=True)
    # Note: Still requires validation if 'host' is untrusted.
    ```

### 3. Insecure Deserialization (Pickle)
*   **Location:** `vulnerable-app.py` (Chunk 0), Lines 50-53 (`import_data` function).
*   **CWE:** **CWE-502 (Deserialization of Untrusted Data)**.
*   **Description:** The `pickle.loads()` function is used to deserialize data from an untrusted source (`request.data`). `pickle` is not safe for untrusted input because it can execute arbitrary Python code during deserialization.
*   **Tainted Data Flow:**
    *   **Source:** `request.data` (Untrusted Network Input).
    *   **Taint Propagation:** Passed to `pickle.loads()`.
    *   **Sink:** Execution of arbitrary Python bytecode within the process.
*   **Framework Reference:**
    *   **MITRE ATT&CK T1203:** Exploits software vulnerabilities to execute code.
*   **Severity:** **Critical**
*   **Remediation:** Do not use `pickle` for untrusted data. Use `json` or `msgpack` (with strict schema validation) instead.
    ```python
    # VULNERABLE: pickle.loads(data)

    # FIXED: Use JSON
    import json
    data = request.data
    imported = json.loads(data) # Safe, no code execution
    ```

### 4. Server-Side Request Forgery (SSRF)
*   **Location:** `vulnerable-app.py` (Chunk 0), Lines 56-59 (`fetch_url` function).
*   **CWE:** **CWE-918 (Server-Side Request Forgery)**.
*   **Description:** The application accepts a URL from the client (`request.json.get('url')`) and passes it directly to `requests.get()` without validation. This allows an attacker to make the server request internal resources (e.g., `http://localhost:6379`, `http://169.254.169.254/latest/meta-data/`) that are not accessible from the public internet.
*   **Tainted Data Flow:**
    *   **Source:** `request.json.get('url')` (Untrusted Input).
    *   **Taint Propagation:** Passed directly to `requests.get()`.
    *   **Sink:** Network Request to arbitrary destination.
*   **Framework Reference:**
    *   **MITRE ATT&CK TA0002:** Execution of malicious code via network requests (often leading to data exfiltration).
*   **Severity:** **High**
*   **Remediation:** Validate the URL scheme and IP address range before making the request. Block internal IP ranges.
    ```python
    # VULNERABLE: Direct request to user input

    # FIXED: Basic validation (block non-http/https and internal IPs)
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

### 5. Insecure YAML Deserialization
*   **Location:** `vulnerable-app.py` (Chunk 0), Lines 62-65 (`load_config` function).
*   **CWE:** **CWE-502 (Deserialization of Untrusted Data)**.
*   **Description:** The code uses `yaml.load()` (the legacy, unsafe version) on untrusted input. The `yaml.load()` function allows arbitrary code execution if the YAML payload is crafted to contain a `!!python/object` tag.
*   **Tainted Data Flow:**
    *   **Source:** `request.data.decode()` (Untrusted Input).
    *   **Taint Propagation:** Passed to `yaml.load()`.
    *   **Sink:** Execution of arbitrary Python code.
*   **Framework Reference:**
    *   **MITRE ATT&CK T1203:** Exploits software vulnerabilities to execute code.
*   **Severity:** **Critical**
*   **Remediation:** Use `yaml.safe_load()` which restricts the types of objects that can be constructed.
    ```python
    # VULNERABLE: yaml.load(data)

    # FIXED: yaml.safe_load(data)
    config = yaml.safe_load(config_data)
    ```

### 6. Path Traversal (File Access)
*   **Location:** `vulnerable-app.py` (Chunk 0), Lines 59-63 (`get_file` function).
*   **CWE:** **CWE-22 (Improper Input Validation)**.
*   **Description:** The filename is passed directly to `os.path.join()` without sanitization. An attacker can use `../` sequences to read files outside the intended `/data/uploads` directory (e.g., `/etc/passwd`).
*   **Tainted Data Flow:**
    *   **Source:** `filename` from URL path (`<path:filename>`).
    *   **Taint Propagation:** Passed to `os.path.join()`.
    *   **Sink:** `open()` system call.
*   **Framework Reference:**
    *
