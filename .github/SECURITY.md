# Security Policy

## Reporting a vulnerability

**Report privately. Do not open a public issue, pull request, or discussion for an undisclosed
vulnerability.**

Use GitHub's private vulnerability reporting, which is enabled on this repository:

**https://github.com/cuga-project/cuga-agent/security/advisories/new**

The report is visible only to you and the maintainers. If you cannot use that form, open a public
issue that says only that you have a security report and asks for a private channel — no details.

### What to include

The more of this you can provide, the faster we can confirm and fix:

- The version affected (`cuga` release, or the commit if you are running from source).
- How CUGA was deployed — which services were running, what was reachable from where, and whether
  the registry or main server was bound to a non-loopback address.
- A reproduction: a request, script, or test that demonstrates the issue. A self-contained
  reproduction that needs no model provider key is ideal, but not required.
- What the reproduction proves — the file read, the credential used, the boundary crossed.
- Any impact you think we will underestimate.

### What to expect

- **Acknowledgement within 5 business days**, confirming we received the report.
- **An initial assessment within 10 business days**, telling you whether we consider it a
  vulnerability, our severity view, and roughly when a fix will land.
- Progress updates as the fix moves, and notice before the advisory is published.

If you have not heard from us within those windows, please follow up on the report thread — a
missed notification is more likely than a decision not to respond.

## Supported versions

CUGA is pre-1.0 and moves quickly. Security fixes land in the next release cut from `main`; we do
not backport to earlier minor versions.

| Version | Supported |
| --- | --- |
| Latest `0.3.x` release | Yes |
| Any earlier release | No — upgrade to the latest |

If you are pinned to an older version and cannot upgrade, say so in your report and we will tell
you which change to apply, though we will not be publishing a patched build for it.

## Scope

CUGA is an agent harness that runs model-generated code, calls third-party APIs with stored
credentials, and serves a local web UI. That shape makes the boundary worth stating explicitly.

### In scope

- Authentication or authorization bypass in the main server, the tool registry, or the secrets API.
- Path traversal, SSRF, injection, or any request-reachable flaw that reads or writes data outside
  its intended directory or host.
- Disclosure of stored credentials or secrets — through API responses, logs, traces, the activity
  tracker, or exported trajectories.
- Escape from the code-execution sandbox to the host beyond the level of access the sandbox is
  documented to allow.
- Prompt injection **that crosses a security boundary** — for example, causing exfiltration of a
  stored credential, or reaching a host or file the operator never granted.

### Out of scope

- **Model behaviour on its own.** The agent producing a wrong, unhelpful, or undesirable answer is
  a bug, not a vulnerability. Report it as a normal issue. Prompt injection is in scope only where
  it crosses a boundary, as above.
- **Attacks that assume an already-compromised host**, a malicious operator, or an attacker who
  already holds the credentials in question.
- **`docs/examples`.** These are standalone illustrations, not shipped code.
- **Dependency advisories with no demonstrated reachable path** in CUGA. Dependabot already tracks
  our dependencies; a report is useful when you can show the vulnerable path is actually reached.
- **Best-practice observations with no demonstrated impact** — a missing header, a scanner finding
  with no exploit behind it.

### Deployment choices you should know about

Some defaults are chosen for local development and are not safe on an untrusted network. These are
documented posture, not undisclosed flaws, but reports that demonstrate concrete impact in a
realistic deployment are still welcome:

- Services may bind to all interfaces rather than loopback. Verify your own exposure rather than
  assuming CUGA is unreachable.
- The code-execution sandbox is a containment boundary, not a hostile-multi-tenant boundary. Do not
  run untrusted third-party tasks on infrastructure you care about without your own isolation.

If you are unsure whether something is in scope, report it. We would rather triage an
out-of-scope report than miss a real one.

## How we handle a report

1. We confirm the report privately and open a draft security advisory.
2. We assess impact and assign a CVSS score and CWE.
3. We develop the fix in the advisory's temporary private fork, so it is not public before the
   advisory is ready.
4. We release a version containing the fix.
5. We publish the advisory and request a CVE. CVEs for this repository are issued by GitHub,
   which acts as the CVE Numbering Authority for advisories filed by maintainers of a hosted
   open-source project.

We ask that you keep the report confidential until the advisory is published. We aim to publish
within 90 days of the report; if a fix is taking longer, we will tell you why rather than let the
date pass in silence.

## Credit

Reporters are credited by name on the published advisory unless you ask us not to be. Tell us how
you would like to be identified.
