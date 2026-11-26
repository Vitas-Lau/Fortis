# T04 — iam-policy.json

- engagement_id: `baseline-lab2-iam` (Lab2-IAM)
- analyst mode (`role`): `general`
- rag_level: `standard`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- latency_sec: **50.53**
- history_turns_sent: 2
- prompt: Which finding is highest risk and why? For each finding, cite the exact section and map it to NIST CSF, CIS Controls, or ISO 27001 control IDs.

- ground_truth: 4/4 (found: principal_star, wildcard_action, mfa_denial, cloudtrail_stop_delete; partial: none; missing: none)
- citations_verified_local: 6
- citations_corpus_gap: 3
- citations_invented: 0
- withdrawn_csf_ids: NIST_CSF:PR.AC

---

## Verbatim response

Based on the `iam-policy.json` provided, the **highest risk finding** is the **`FullAdminAccess`** statement (Sid: "FullAdminAccess").

### Highest Risk Finding: `FullAdminAccess`
*   **Exact Section:** `iam-policy.json` — Chunk 0, Statement 1.
    ```json
    {
      "Sid": "FullAdminAccess",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "*",
      "Resource": "*"
    }
    ```
*   **Risk Rating:** **Critical**
*   **Justification:** This policy grants **unrestricted administrative privileges** to **any AWS account** (`Principal: "*"`) and **any action** (`Action: "*"`) on **any resource** (`Resource: "*"`).
    *   **Impact:** An attacker who compromises a single AWS account (or any entity with this policy) gains immediate, total control over the entire cloud environment. They can pivot laterally, delete critical infrastructure, exfiltrate all data, and modify other policies to lock out legitimate administrators.
    *   **Framework Violation:**
        *   **NIST CSF PR.AC-5 (Access Control):** Violates the principle of least privilege by granting maximum permissions to an undefined principal.
        *   **CIS Controls v8 Control 5 (Secure Configuration of Identity Management):** Explicitly prohibits granting administrative access to unauthenticated or unverified users.
        *   **ISO 27001 A.5.4 (Access Control):** Fails to ensure that access is granted only to authorized personnel with the minimum privileges necessary.

---

### Secondary High-Risk Findings & Framework Mapping

#### 2. Log Tampering Capability (`AllowCloudTrailWrite`)
*   **Exact Section:** `iam-policy.json` — Chunk 0, Statement 13.
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
*   **Risk Rating:** **Critical**
*   **Justification:** This allows any user to disable logging or delete the audit trail, effectively erasing evidence of malicious activity.
    *   **Framework Violation:**
        *   **NIST CSF DE.CM-1 (Detection and Response):** Prevents the detection of security events by removing the audit log.
        *   **CIS Controls v8 Control 12 (Monitor and Respond to Security Events):** Violates the requirement for continuous monitoring and log retention.
        *   **ISO 27001 A.12.4 (Audit Events):** Fails to ensure audit events are recorded and protected from tampering.

#### 3. Denial of MFA Enforcement (`DenyMFASetup`)
*   **Exact Section:** `iam-policy.json` — Chunk 0, Statement 6.
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
*   **Risk Rating:** **High**
*   **Justification:** This policy explicitly prevents the setup of Multi-Factor Authentication (MFA) for any user. Since the `FullAdminAccess` policy allows `*` actions, this `Deny` statement is likely being overridden by the broader `Allow` statement, rendering the MFA restriction ineffective. Even if it applied, it creates a single point of failure for authentication.
    *   **Framework Violation:**
        *   **NIST CSF PR.AC-5 (Access Control):** Fails to enforce MFA for privileged access.
        *   **CIS Controls v8 Control 5:** Requires MFA for all administrative access.
        *   **HIPAA 164.310(a)(2)(ii):** Requires unique identifiers and multi-factor authentication for remote access.

#### 4. Excessive Data Access (`AllowAllS3Buckets`, `AllowSecretsManagerRead`, `AllowKMSDecrypt`)
*   **Exact Sections:** `iam-policy.json` — Chunk 0, Statements 2, 9, 10.
    ```json
    // S3 Access
    {
      "Sid": "AllowAllS3Buckets",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::*:root" },
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": "*"
    }
    // Secrets & KMS
    {
      "Sid": "AllowSecretsManagerRead",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "*"
    }
    {
      "Sid": "AllowKMSDecrypt",
      "Effect": "Allow",
      "Principal": "*",
      "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
      "Resource": "*"
    }
    ```
*   **Risk Rating:** **High**
*   **Justification:** These policies grant broad read/write access to secrets, encryption keys, and data storage to any principal. Combined with the `FullAdminAccess` policy, this allows an attacker to decrypt sensitive data and exfiltrate it without needing specific user identities.
    *   **Framework Violation:**
        *   **PCI DSS Req 1 (PCI DSS v4.0):** Fails to protect cardholder data environments with strict access controls.
        *   **NIST CSF PR.AC-5:** Violates least privilege regarding sensitive data access.
        *   **NIST SP 800-53 SC-13:** Fails to govern cryptographic modules properly if keys are accessible to unauthorized principals.

---

### Summary of Framework Mapping

| Finding | NIST CSF | CIS Controls | ISO 27001 | PCI DSS |
| :--- | :--- | :--- | :--- | :--- |
| **FullAdminAccess** | **PR.AC-5** (Access Control) | **Control 5** (Identity Mgmt) | **A.5.4** (Access Control) | N/A |
| **Log Tampering** | **DE.CM-1** (Detection & Response) | **Control 12** (Monitor Events) | **A.12.4** (Audit Events) | N/A |
| **Deny MFA** | **PR.AC-5** (Access Control) | **Control 5** (Identity Mgmt) | **A.5.4** (Access Control) | N/A |
| **Data Access** | **PR.AC-5** (Access Control) | N/A | **A.8.1** (Data Protection) | **Req 1** (Protect Data) |

### Consultant Recommendation
The `FullAdminAccess` policy is the primary vector for total compromise. **Immediate remediation** is required to replace `Principal: "*"`, `Action: "*"`, and `Resource: "*"` with specific IAM roles, users, and resource ARNs. Additionally, the `DenyMFASetup` policy must be removed or corrected to enforce MFA, and the `AllowCloudTrailWrite` policy must be revoked to ensure audit integrity.
