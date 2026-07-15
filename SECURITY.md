# Security and privacy

## Reporting a vulnerability

Open a GitHub security advisory for vulnerabilities. Do not include real candidate records, credentials or application exports in public issues.

## Data boundary

This repository is designed to contain code and fictional examples only. Real candidate profiles, résumés, contact details, application histories and ATS session material must remain outside the repository.

Before publishing changes, run:

```bash
interview-ops audit .
```

The audit is a guardrail, not a complete secret scanner. Review the staged diff before every push.
