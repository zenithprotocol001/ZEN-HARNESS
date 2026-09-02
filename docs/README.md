# DHC docs

This directory holds the architectural, security, plugin-authoring, and
process documentation for the **DeepSeek Harness Creation (DHC) benchmark**.

## Index

| File | Purpose |
|---|---|
| [`architecture.md`](architecture.md) | System architecture: cordis port, 10 core modules, turn/step waterfall, plugin marketplace |
| [`security-model.md`](security-model.md) | Defense-in-depth, CSP, capability policy, manifest integrity, threat model |
| [`plugin-authoring.md`](plugin-authoring.md) | How to write a Cordis plugin for the harness: `@plugin`, manifest schema, SHA pinning, lifecycle |
| [`SHA-PINNING.md`](SHA-PINNING.md) | Currently locked SHA-256 digests for the 5 bundled plugins |
| [`adr/0001-three-tab-ui.md`](adr/0001-three-tab-ui.md) | Why the web UI is split into 3 tabs and HTML injection is localized |
| [`adr/0002-plugin-marketplace-with-sha-integrity.md`](adr/0002-plugin-marketplace-with-sha-integrity.md) | Why every plugin ships a SHA-256 manifest and how the loader verifies it |
| [`adr/0003-ephemeral-port-and-bearer-token.md`](adr/0003-ephemeral-port-and-bearer-token.md) | Why C1 picks an ephemeral port and a per-launch bearer token |

Process docs (versioned at the repo root):

- [`../CHANGELOG.md`](../CHANGELOG.md) — version history
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — how to add a module or plugin
- [`../GLOSSARY.md`](../GLOSSARY.md) — terminology
- [`../relay/MANIFEST.txt`](../relay/MANIFEST.txt) — per-version ship manifest

## Reading order

If you are new to the project, read in this order:

1. `architecture.md` — what the system is
2. `security-model.md` — what it defends against
3. `plugin-authoring.md` — how to extend it
4. `adr/` — why it is shaped this way
5. `../GLOSSARY.md` — when a term in code or chat is unfamiliar
