# examples-dev — in-progress examples

Termin specifications that are being iterated on but are **not yet ready**
to ship as canonical examples. Contents of this directory:

- do NOT participate in the release pipeline (`util/release.py` walks
  `examples/`, not this folder)
- are NOT compiled into the conformance fixtures or `ir_dumps/`
- MAY use DSL shapes that the PEG grammar parses only via the fallback
  path, or analyzer checks that are still being iterated

When an example in this folder stabilizes — grammar supported
end-to-end, analyzer clean, conformance tests written — move it to
`examples/` and re-run the release script.

## Current contents

- `agent_chatbot2.termin` — multi-content agent sketch (access to both
  `messages` and `products`). Blocked on the v0.8.1 PEG gap for
  `Accesses <content>, <content>` line shape (falls back to Python
  string parsing today; enforcement works, but fidelity test fails).
  Probable rename when promoted: `agent_data_streams.termin` or
  similar.
