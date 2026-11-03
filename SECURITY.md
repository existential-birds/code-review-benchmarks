# Security

Report a vulnerability privately through
[GitHub's vulnerability reporting](https://github.com/existential-birds/code-review-benchmarks/security/advisories/new),
not in a public issue. If that form isn't available, open an issue asking for a
private contact and leave out the details.

The suite runs untrusted repository contexts next to your Codex and Pi logins.
[The AACR guide](benchmarks/aacr/README.md#logins) describes what each container
can see. Ways around those limits are in scope. The reviewer's network access while
it holds your Codex session is a known, documented risk.
