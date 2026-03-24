# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes       |
| 0.1.x   | No        |

Older versions are not patched. Please upgrade to the latest release before reporting a vulnerability.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please use [GitHub Security Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) to report privately. You can find the "Report a vulnerability" button on the Security tab of this repository.

Include:

- A clear description of the issue and its impact
- Steps to reproduce (proof-of-concept code is helpful)
- Your assessment of severity

We aim to acknowledge reports within 72 hours and to publish a fix or advisory within 14 days of confirmation.

## Scope

The following classes of issues are in scope:

- **Injection detection bypass** — crafted WhatsApp messages that escape access control checks, role tagging, or policy enforcement and execute restricted commands
- **Authentication bypass** — gaining control of the bot without a valid host JID
- **Role escalation** — participant gaining host-level access, or bypassing DM elevation approval
- **Tool/resource policy bypass** — calling tools or reading resources not in `allowed_tools`/`allowed_resources`
- **JID / session leakage** — one chat's session data (game state, participant list, conversation history) becoming visible to another chat
- **Cross-app memory access** — community app reading another app's memory via resource patterns

Out-of-scope: rate-limit abuse, spam, denial-of-service against the WhatsApp network itself.

## Baileys bridge note

`c3/baileys_bridge.js` wraps [Baileys](https://github.com/WhiskeySockets/Baileys), an unofficial WhatsApp Web client. Use of this bridge may be subject to WhatsApp's Terms of Service. c3-py does not endorse or encourage ToS violations. Security issues specific to the Baileys library should be reported upstream to that project; issues in how c3-py *integrates* the bridge are in scope here.
