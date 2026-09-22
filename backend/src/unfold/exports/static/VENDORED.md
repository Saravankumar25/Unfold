# Vendored static assets

These files are loaded **only** from the local package at PDF render time (LLD §19). The export page blocks every
network request, so nothing here may be fetched from a CDN.

| File | Package | Version | Source | SHA-256 |
|---|---|---|---|---|
| `mermaid.min.js` | `mermaid` (npm) | 11.17.2 | `https://registry.npmjs.org/mermaid/-/mermaid-11.17.2.tgz` → `package/dist/mermaid.min.js` | `581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8` |
| `mermaid.LICENSE` | `mermaid` (npm) | 11.17.2 | same tarball → `package/LICENSE` (MIT) | — |

Tarball integrity (npm `dist.integrity`):
`sha512-V6K3C8EBdEsPFZXSKMJe6ppQOENxuHARr9GvHX4hh47lAbhMRD9qf4oEK7LoaRQxULMa80/qt5gHO73aCleBBg==`

To upgrade: `npm pack mermaid@<version>` in a temp dir, extract `package/dist/mermaid.min.js`, replace the file,
update this table (`sha256sum mermaid.min.js`), and run `uv run pytest -m integration tests/integration/test_exports_pdf.py`.
`tests/unit/test_exports_render.py` checks that the file's SHA-256 matches this table.
