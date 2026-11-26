# B02 — iam-policy.json @ RAG_LEVEL=basic

- engagement_id: `baseline-rag-basic-rag-basic-s2` (Baseline-RAG-basic / RAG-basic-S2)
- analyst mode (`role`): `general`
- rag_level (verified in server env): `basic`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- latency_sec: **49.06**
- prompt: Which finding is highest risk and why? For each finding, cite the exact section and map it to NIST CSF, CIS Controls, or ISO 27001 control IDs.

- ground_truth: 4/4 (found: principal_star, wildcard_action, mfa_denial, cloudtrail_stop_delete; partial: none; missing: none)
- citations_verified_local: 7
- citations_corpus_gap: 5
- citations_invented: 0
- withdrawn_csf_ids: NIST_CSF:DE.AE-01; NIST_CSF:PR.AC-04; NIST_CSF:PR.AC-05; NIST_CSF:PR.AC-06

---

## Verbatim response

Based on the `iam-policy.json` excerpts provided, the **highest risk finding** is the **`FullAdminAccess` policy statement granting `Principal: "*"`, `Action: "*"`, and `Resource: "*"`**.

### 1. Highest Risk Finding Analysis

**Finding:** The policy `Sid: "FullAdminAccess"` allows any principal (any user, role, or service) to perform any action on any resource.
*   **Exact Section:** `[iam-policy.json — chunk 0]`, `Statement[0]` (Sid: "FullAdminAccess").
*   **Configuration Detail:**
    ```json
    {
      "Sid": "FullAdminAccess",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "*",
      "Resource": "*"
    }
    ```

**Why it is Critical:**
This is a "wildcard" permission that effectively grants root-level access to the entire AWS account to any entity that can read this policy. In the context of AWS, `Principal: "*"` combined with `Action: "*"` means an attacker who compromises a single low-privilege user, a leaked credential, or a compromised Lambda function can immediately assume full administrative control over EC2, RDS, S3, IAM itself, and CloudTrail. This allows for lateral movement, data exfiltration, and complete account takeover.

**Framework Mapping & Severity:**
*   **Severity:** **Critical**
*   **NIST CSF 2.0:** Maps to **PR.AC-5 (Enforce Least Privilege)** and **PR.AC-6 (Enforce Least Privilege for Cloud Resources)**. This policy violates the fundamental principle of granting only the minimum permissions necessary.
*   **CIS Controls:** Violates **CIS Control 5 (Secure Configuration of Cloud Workloads)** and specifically **CIS Control 1.1 (Identity and Access Management)** regarding the principle of Least Privilege.
*   **ISO 27001:** Violates **A.9.4.2 (Principle of least privilege)** and **A.12.6.1 (Access control – authorization)**.

---

### 2. Secondary High-Risk Findings

While the wildcard admin access is the primary vector, the following findings compound the risk by allowing the attacker to hide their tracks and further escalate privileges.

#### Finding A: Ability to Disable Logging and Delete Trails
*   **Exact Section:** `[iam-policy.json — chunk 0]`, `Statement[9]` (Sid: "AllowCloudTrailWrite").
*   **Configuration Detail:**
    ```json
    {
      "Sid": "AllowCloudTrailWrite",
      "Effect": "Allow",
      "Principal": "*",
      "Action": [
        "cloudtrail:StopLogging",
        "cloudtrail:DeleteTrail"
      ],
      "Resource": "*"
    }
    ```
*   **Analysis:** This allows any user to stop CloudTrail logging or delete the trail. This is a "log tampering" attack vector. An attacker can use the `FullAdminAccess` privilege to immediately erase evidence of their intrusion, making incident detection and forensic analysis impossible.
*   **Framework Mapping:**
    *   **NIST CSF 2.0:** **DE.AE-1 (Detect Anomalous Activity)** and **DE.AE-2 (Detect Compromised Accounts)**. If logs are deleted, detection capabilities are nullified.
    *   **CIS Controls:** **CIS Control 12 (Monitor and Control Cloud Workloads)** and **CIS Control 16 (Incident Response)**.
    *   **ISO 27001:** **A.12.4.1 (Logging and monitoring)** and **A.12.4.2 (Log management)**.

#### Finding B: Ability to Create Users and Keys
*   **Exact Section:** `[iam-policy.json — chunk 0]`, `Statement[4]` (Sid: "AllowIAMUserCreation").
*   **Configuration Detail:**
    ```json
    {
      "Sid": "AllowIAMUserCreation",
      "Effect": "Allow",
      "Principal": "*",
      "Action": [
        "iam:CreateUser",
        "iam:CreateAccessKey",
        "iam:AttachUserPolicy",
        "iam:PutUserPolicy"
      ],
      "Resource": "*"
    }
    ```
*   **Analysis:** Combined with the wildcard admin access, this allows an attacker to create new IAM users and attach the `FullAdminAccess` policy to them, effectively creating "shadow administrators" or new attack vectors that are difficult to audit.
*   **Framework Mapping:**
    *   **NIST CSF 2.0:** **PR.AC-5 (Enforce Least Privilege)**.
    *   **CIS Controls:** **CIS Control 1.1 (Identity and Access Management)**.

#### Finding C: Denial of MFA
*   **Exact Section:** `[iam-policy.json — chunk 0]`, `Statement[5]` (Sid: "DenyMFASetup").
*   **Configuration Detail:**
    ```json
    {
      "Sid": "DenyMFASetup",
      "Effect": "Deny",
      "Principal": "*",
      "Action": [
        "iam:CreateVirtualMFADevice",
        "iam:EnableMFADevice",
        "iam:ResyncMFADevice"
      ],
      "Resource": "*"
    }
    ```
*   **Analysis:** This policy explicitly prevents the setup of MFA. While this might be intended to prevent "MFA fatigue" attacks or ensure only specific users can set up MFA, in the context of a policy granted to `Principal: "*"`, it creates a massive security gap. It prevents the organization from enforcing Multi-Factor Authentication on any account that can read this policy.
*   **Framework Mapping:**
    *   **NIST CSF 2.0:** **PR.AC-4 (Enforce Multi-Factor Authentication)**.
    *   **CIS Controls:** **CIS Control 1.1 (Identity and Access Management)**.
    *   **ISO 27001:** **A.9.4.3 (Multi-factor authentication)**.

---

### 3. Remediation Recommendations

To address these findings, implement the following concrete steps:

1.  **Remove or Restrict `FullAdminAccess`:**
    *   **Action:** Delete the `FullAdminAccess` policy statement entirely.
    *   **Remediation:** Implement a **Least Privilege** model. Create specific policies for each role (e.g., `AdminAccess`, `S3ReadOnly`, `LambdaInvoker`) and assign them to specific principals. Ensure no single policy grants `Action: "*"` to `Principal: "*"`.
    *   **Reference:** NIST CSF PR.AC-5; CIS Control 1.1.

2.  **Secure CloudTrail Logging:**
    *   **Action:** Remove the `cloudtrail:StopLogging` and `cloudtrail:DeleteTrail` actions from the `Principal: "*"` policy.
    *   **Remediation:** Grant `cloudtrail:CreateTrail` and `cloudtrail:UpdateTrail` only to specific administrative roles, and ensure the `cloudtrail:StopLogging` action is restricted to a dedicated "Security Admin" role with MFA enforcement.
    *   **Reference:** NIST CSF DE.AE-1; CIS Control 12.

3.  **Enforce MFA:**
    *   **Action:** Remove the `DenyMFASetup` policy or modify it to only deny MFA setup for specific non-critical accounts, not globally.
    *   **Remediation:** Ensure that all IAM users and roles require MFA for privileged actions. If MFA setup is restricted, ensure there is a secure, audited process for enabling it for necessary accounts.
    *   **Reference:** NIST CSF PR.AC-4; ISO 27001 A.9.4.3.

4.  **Review Other Wildcards:**
    *   **Action:** Review `AllowAllS3Buckets`, `AllowLambdaInvoke`, `AllowEC2FullAccess`, and `AllowRDSFullAccess`.
    *   **Remediation:** Replace `Resource: "*"` with specific ARNs (e.g., `arn:aws:s3:::my-bucket/*`) and restrict `Action` to only what is required for the specific service role.
    *   **Reference:** NIST CSF PR.AC-5; CIS Control 5.

**Conclusion:** The immediate priority is removing the `FullAdminAccess` wildcard policy. This single change reduces the attack surface from "total account compromise" to "limited resource access," which is the most significant step toward compliance with NIST CSF and CIS Controls.
