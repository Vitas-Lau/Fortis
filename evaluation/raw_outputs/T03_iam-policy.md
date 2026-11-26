# T03 — iam-policy.json

- engagement_id: `baseline-lab2-iam` (Lab2-IAM)
- analyst mode (`role`): `general`
- rag_level: `standard`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- latency_sec: **36.44**
- history_turns_sent: 0
- prompt: What are the security issues in this IAM policy?

- ground_truth: 4/4 (found: principal_star, wildcard_action, mfa_denial, cloudtrail_stop_delete; partial: none; missing: none)
- citations_verified_local: 5
- citations_corpus_gap: 3
- citations_invented: 0
- withdrawn_csf_ids: NIST_CSF:PR.AC

---

## Verbatim response

Based on the `iam-policy.json` provided, there are **four critical security issues** that violate fundamental principles of least privilege, defense in depth, and auditability. These issues expose the organization to unauthorized data exfiltration, privilege escalation, and the ability to evade detection.

### 1. Wildcard Principal and Action: "Full Admin Access"
*   **Finding:** The policy `Sid: "FullAdminAccess"` grants `Principal: "*"`, `Action: "*"`, and `Resource: "*"`.
*   **Framework Violation:**
    *   **NIST CSF PR.AC-5 (Access Control):** Requires granting access only to authorized users and roles with the minimum privileges necessary.
    *   **CIS Controls v8 Control 5 (Secure Configuration of Identity Management):** Mandates the use of least privilege and prohibits wildcard principals for administrative actions.
    *   **ISO 27001 A.5.4:** Requires employees to apply security in line with policy; a wildcard policy makes it impossible to enforce specific security policies on specific users.
*   **Risk Rating:** **Critical**
*   **Justification:** This policy effectively grants any AWS account (including compromised accounts or external actors) complete administrative control over the entire AWS environment. An attacker with this policy can pivot from a single compromised instance to controlling the entire cloud infrastructure, including deleting resources, modifying other policies, or launching attacks.

### 2. Denial of MFA Enforcement
*   **Finding:** The policy `Sid: "DenyMFASetup"` explicitly denies actions related to creating, enabling, or resyncing MFA devices (`iam:CreateVirtualMFADevice`, `iam:EnableMFADevice`, `iam:ResyncMFADevice`) for `Principal: "*"`.
*   **Framework Violation:**
    *   **NIST CSF PR.AC-5:** Requires multi-factor authentication (MFA) for privileged access.
    *   **CIS Controls v8 Control 5:** Requires MFA for all administrative access.
    *   **HIPAA 164.310(a)(2)(ii):** Requires unique identifiers for each user and multi-factor authentication for remote access.
*   **Risk Rating:** **Critical**
*   **Justification:** This is a direct contradiction to security best practices. By denying the ability to set up MFA, the policy forces administrators to rely solely on passwords. If a password is compromised, the account remains fully functional. This violates the principle of defense in depth and creates a single point of failure for authentication.

### 3. Log Tampering Capability
*   **Finding:** The policy `Sid: "AllowCloudTrailWrite"` grants `cloudtrail:StopLogging` and `cloudtrail:DeleteTrail` to `Principal: "*"`.
*   **Framework Violation:**
    *   **NIST CSF DE.CM-1 (Detection and Response):** Requires the ability to detect and respond to security events; log tampering prevents this.
    *   **CIS Controls v8 Control 12 (Monitor and Respond to Security Events):** Requires continuous monitoring and log retention.
    *   **ISO 27001 A.12.4 (Audit Events):** Requires the organization to monitor and record audit events and ensure they are not tampered with.
*   **Risk Rating:** **Critical**
*   **Justification:** This allows any user to stop logging or delete the CloudTrail trail. This effectively erases the audit trail of all administrative actions, making incident detection impossible and providing an attacker with "cover of darkness" to operate undetected.

### 4. Excessive S3 and Resource Access
*   **Finding:**
    *   `Sid: "AllowAllS3Buckets"` grants `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` to `Principal: "*"`.
    *   `Sid: "AllowEC2FullAccess"` and `Sid: "AllowRDSFullAccess"` grant full EC2 and RDS actions to `Principal: "*"`.
*   **Framework Violation:**
    *   **NIST CSF PR.AC-5:** Least privilege principle.
    *   **PCI DSS Requirement 7:** Requires access controls to limit access to cardholder data environments to authorized personnel only.
    *   **ISO 27001 A.8.1:** Protects information stored on user endpoint devices (and by extension, cloud storage).
*   **Risk Rating:** **High**
*   **Justification:** While these actions are necessary for some functions, granting them to `Principal: "*"` means *anyone* can read, write, delete, and list all S3 buckets, and manage all EC2/RDS instances. This allows for lateral movement, data exfiltration, and destruction of critical assets without needing specific user identities.

### Remediation Recommendations

1.  **Remove Wildcard Principals:** Replace `Principal: "*"` with specific IAM users, roles, or groups. Ensure every policy has a defined identity.
2.  **Apply Least Privilege:**
    *   Remove `Action: "*"` and replace with specific actions required for the job.
    *   Restrict `Resource: "*"` to specific ARNs (e.g., specific S3 buckets, specific VPCs) rather than all resources.
3.  **Enforce MFA:** Remove the `DenyMFASetup` policy. Implement a separate policy that *requires* MFA for users with administrative privileges (e.g., `iam:CreateAccessKey` should only be allowed for users with MFA enabled).
4.  **Secure CloudTrail:** Remove `cloudtrail:StopLogging` and `cloudtrail:DeleteTrail` from the policy. Ensure the CloudTrail service role has the necessary permissions to manage its own logging, but deny these specific destructive actions to application users.
5.  **Segment Access:** If broad access is truly required, implement AWS Organizations Service Control Policies (SCPs) to enforce stricter limits at the account level, overriding per-user policies where necessary.

**Summary:** The current policy configuration represents a "master key" scenario where any entity can do anything, including destroying evidence and bypassing authentication controls. Immediate remediation is required to align with NIST CSF, CIS Controls, and ISO 27001 requirements.
