# F02 — iam-policy.json @ RAG_LEVEL=full

- engagement_id: `baseline-rag-full-rag-full-s2` (Baseline-RAG-full / RAG-full-S2)
- analyst mode (`role`): `general`
- rag_level (verified in server env): `full`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- rerank.is_available(): `True`
- latency_sec: **48.60**
- prompt: Which finding is highest risk and why? For each finding, cite the exact section and map it to NIST CSF, CIS Controls, or ISO 27001 control IDs.

- ground_truth: 4/4 (found: principal_star, wildcard_action, mfa_denial, cloudtrail_stop_delete; partial: none; missing: none)
- citations_verified_local: 8
- citations_corpus_gap: 3
- citations_invented: 0
- withdrawn_csf_ids: NIST_CSF:PR.AC-05

---

## Verbatim response

Based on the `iam-policy.json` excerpts provided, the **highest risk finding** is the **`FullAdminAccess` policy statement granting unrestricted permissions to all principals.**

### 1. Highest Risk Finding: `FullAdminAccess`
**Location:** `iam-policy.json` — Chunk 0, Line 4 (`Sid`: "FullAdminAccess")
**Policy Snippet:**
```json
{
  "Sid": "FullAdminAccess",
  "Effect": "Allow",
  "Principal": "*",
  "Action": "*",
  "Resource": "*"
}
```

**Analysis & Framework Mapping:**
*   **The Issue:** This policy grants `Principal: "*"` (any AWS entity) the ability to perform `Action: "*"` (any AWS action) on `Resource: "*"`. This is a "wildcard" policy that effectively gives any user or role full administrative control over the entire AWS account.
*   **Severity:** **Critical**
*   **Justification:** This represents a catastrophic failure of the Principle of Least Privilege. If an attacker compromises a single low-privilege account (e.g., via a phishing email or credential theft), they can immediately execute `iam:CreateAccessKey` to create their own admin credentials, or directly delete the account (`ec2:TerminateInstance` on the root, `rds:DeleteDBInstance`, etc.). There is no containment.
*   **Framework Controls:**
    *   **NIST CSF (2.0):** ID **IM-1 (Identity Management)** and ID **PR.AC-5 (Access Control)**. The policy fails to enforce least privilege and allows unrestricted access, violating the requirement to limit access to only necessary resources.
    *   **CIS Controls:** **CIS Control 5 (Secure Configuration of Cloud Workloads)** and **CIS Control 6 (Identity Management)**. Specifically, it violates the requirement to "Ensure that IAM users and roles have the minimum privileges necessary to perform their job functions."
    *   **ISO 27001:** **A.9.2.2 (Access rights)** and **A.9.2.3 (Access rights review)**. The policy fails to ensure access rights are assigned based on the principle of least privilege and lacks a mechanism for regular review due to the lack of specific principals.

---

### 2. Secondary High-Risk Finding: `DenyMFASetup` (Contradictory & Dangerous)
**Location:** `iam-policy.json` — Chunk 0, Line 19 (`Sid`: "DenyMFASetup")
**Policy Snippet:**
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

**Analysis & Framework Mapping:**
*   **The Issue:** This policy explicitly **denies** the ability to create, enable, or resync MFA devices for *any* principal (`Principal: "*"`).
*   **Severity:** **Critical** (due to contradiction with `FullAdminAccess`, but functionally critical for security posture)
*   **Justification:** This is a direct violation of security best practices. MFA (Multi-Factor Authentication) is the primary defense against credential theft. By denying MFA setup globally, the organization forces reliance on single-factor authentication (passwords) for all users, including administrators.
*   **Framework Controls:**
    *   **NIST CSF (2.0):** ID **IA.AM-1 (Authentication)** and ID **IA.AM-2 (Authorization)**. The policy fails to implement strong authentication mechanisms (MFA) for all users.
    *   **CIS Controls:** **CIS Control 5 (Secure Configuration of Cloud Workloads)** and **CIS Control 6 (Identity Management)**. Violates the requirement to "Ensure that all users have MFA enabled" and "Ensure that privileged accounts use MFA."
    *   **ISO 27001:** **A.9.4.1 (Authentication)** and **A.9.4.2 (Authorization)**. Fails to implement strong authentication and fails to ensure that access is controlled by robust mechanisms.

---

### 3. High-Risk Finding: `AllowCloudTrailWrite` (Log Tampering)
**Location:** `iam-policy.json` — Chunk 0, Line 33 (`Sid`: "AllowCloudTrailWrite")
**Policy Snippet:**
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

**Analysis & Framework Mapping:**
*   **The Issue:** This policy allows any principal to stop logging or delete the CloudTrail trail.
*   **Severity:** **High**
*   **Justification:** CloudTrail is the primary source of audit logs for AWS. If an attacker can stop logging or delete the trail, they can erase their own evidence of the attack (e.g., the `FullAdminAccess` compromise) before being caught. This destroys the integrity of the audit log.
*   **Framework Controls:**
    *   **NIST CSF (2.0):** ID **DE.CM-2 (Detection)** and ID **DE.CM-3 (Detection of Malicious Activity)**. The policy prevents the detection of malicious activity by allowing the deletion of logs.
    *   **CIS Controls:** **CIS Control 12 (Security Monitoring and Malware Analysis)** and **CIS Control 13 (Incident Response)**. Violates the requirement to "Ensure that security events are logged and monitored" and "Ensure that logs are protected from tampering."
    *   **ISO 27001:** **A.12.4.1 (Logging)** and **A.12.4.2 (Log management)**. Fails to ensure that logs are generated, stored, and protected from unauthorized modification.

---

### 4. Medium-Risk Finding: `AllowAllS3Buckets`
**Location:** `iam-policy.json` — Chunk 0, Line 8 (`Sid`: "AllowAllS3Buckets")
**Policy Snippet:**
```json
{
  "Sid": "AllowAllS3Buckets",
  "Effect": "Allow",
  "Principal": {
    "AWS": "arn:aws:iam::*:root"
  },
  "Action": [
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:ListBucket"
  ],
  "Resource": "*"
}
```

**Analysis & Framework Mapping:**
*   **The Issue:** Grants full read/write/delete access to all S3 buckets to the root user of *any* AWS account (`arn:aws:iam::*:root`).
*   **Severity:** **High** (due to the `*` in the principal ARN)
*   **Justification:** While less catastrophic than `FullAdminAccess` (which covers EC2/RDS), allowing any root user to delete or modify S3 data creates significant data loss and exfiltration risks. If a third-party account is compromised, their root user can wipe all S3 data.
*   **Framework Controls:**
    *   **NIST CSF (2.0):** ID **PR.AC-5 (Access Control)**. Violates least privilege regarding data storage.
    *   **GDPR Art.25 (Data Protection by Design):** Fails to restrict access to data necessary for processing, as any external root user can access all data.

---

### Summary of Remediation Priorities

1.  **Immediate Action:** Remove the `FullAdminAccess` policy (`Sid`: "FullAdminAccess"). Implement a new policy granting only the specific principals and actions required for their roles.
2.  **Immediate Action:** Remove the `DenyMFASetup` policy (`Sid`: "DenyMFASetup"). This is a critical security gap that must be reversed to enable MFA for all users.
3.  **Immediate Action:** Remove the `AllowCloudTrailWrite` policy (`Sid`: "AllowCloudTrailWrite"). Ensure no principal has the ability to stop logging or delete trails.
4.  **Review:** Restrict `AllowAllS3Buckets` to specific principals and specific buckets, rather than applying it to `*`.

**Note:** The combination of `FullAdminAccess` and `DenyMFASetup` creates a scenario where an attacker can gain full control of the account without the benefit of the strongest defense (MFA). This is the most dangerous configuration in the file.
