# Security policy

Pickwise stores personal, identity and payroll data, so we take vulnerability reports seriously.

## Reporting a vulnerability

**Don't open a public issue.** Report it privately through GitHub's [private vulnerability reporting](https://github.com/srikary12/pickwise/security/advisories/new) for this repository.

Please include:
- the affected component and version or commit
- steps to reproduce, or a proof of concept
- the impact you believe it has, e.g. cross-tenant data access or PII exposure

What to expect:
- We aim to acknowledge reports within **3 business days** and to give an initial assessment within **10 business days**.
- We'll keep you updated while we work on a fix, and credit you in the advisory unless you'd rather stay anonymous.
- Please give us a reasonable chance to release a fix before you disclose publicly.

## Scope

These are especially in scope:
- **tenant isolation:** Row-Level Security, composite keys, cross-tenant access through any path
- **authentication, sessions, MFA and SSO**
- **authorization and data scopes**
- **handling of restricted data:** PAN, Aadhaar, bank accounts, encryption, blind indexes
- **file uploads:** scanning, type checks, presigned URLs
- **prompt injection via resumes** that affects screening outcomes

## Supported versions

Pickwise is in early development and hasn't made a release yet. Until v1.0, only the latest `main` gets security fixes.
