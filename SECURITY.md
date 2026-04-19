# Security Policy

Termin takes security seriously. This file is the authoritative disclosure channel for vulnerabilities in the compiler and reference runtime. The [public security page at termin.dev](https://termin.dev/security/) summarises this policy and adds the threat model and audit status; this file is the minimum a developer needs to report an issue.

## Reporting a vulnerability

**Email:** `security@termin.dev`

**What to include:**

- A reproducible test case where possible.
- The affected version (compiler version, runtime version, or both).
- The platform and Python version.
- A description of the impact — what an attacker can do, under what conditions.

Plaintext email is acceptable for initial contact. If your report contains sensitive details, request an encrypted channel in your first message and we will coordinate.

**Do not file a public GitHub Issue** for a suspected vulnerability. Use the private email channel.

## What to expect

- **Acknowledgment within 3 business days.** A human response confirming receipt and opening a tracking channel.
- **Initial triage within 10 business days.** An assessment of severity and scope.
- **Coordinated disclosure** with a default 90-day window from acknowledgment to public disclosure. Earlier if the fix lands sooner; later by mutual agreement if the fix is complex.
- **Credit in the changelog** unless you request anonymity. A CVE is requested by the project once a fix is available.

## Scope

**In scope:**

- The Termin compiler (this repository).
- The reference runtime (included in this repository under `termin_runtime/`).
- The [conformance suite](https://github.com/jamieleigh3d/termin-conformance).
- Shipped example applications.

**Out of scope:**

- Third-party runtime implementations of the Termin IR.
- Third-party Compute providers (Tier 3 — custom providers are outside the structural guarantee boundary).
- Applications built on Termin. Those are the responsibility of their own maintainers.
- Attacks by the operator of the runtime. Defense against administrator abuse is a deployment concern.
- Dependencies (report to the upstream maintainer and let us know so we can bump the pinned version).

For the full threat model, runtime hardening details, and audit status, see the [security page on termin.dev](https://termin.dev/security/).

## Coordinated disclosure philosophy

We prefer coordinated disclosure because the Termin runtime, once public and installed, may be deployed in places we don't know about. Giving the ecosystem time to update before public disclosure reduces the window of active exploitation. If you have strong views on timing, say so in your initial report and we will work with you.

## Scope of this file

This file covers only the disclosure channel. Code-level security invariants, the threat model, hardening details, and audit status live on the [public security page at termin.dev](https://termin.dev/security/).
