# Tenets

*Strategic values that guide Termin's design. Unless we know better ones.*

Termin is shaped by five commitments. Each names a tradeoff explicitly: when reasonable-sounding options conflict, the tenets settle which direction we go. They are ordered as a priority stack — earlier tenets win when they conflict with later ones.

---

## 1. Audit over authorship.

The bottleneck on enterprise software is review, not writing. We shrink the audit surface to pre-reviewed structural primitives, leaving only unique business logic for human scrutiny. At the authorship end, AI writes the specification — the language is small enough to fit in a context window, constrained enough that humans can verify what came back. Authors automate; reviewers stay human.

## 2. Enforcement over vigilance.

Security, access control, and confidentiality are the platform's responsibility, not the developer's attention. Interlocking layers — grammar rules, runtime checks, conformance tests, and provider contracts — enforce what the spec declares. A property that depends on someone remembering to add a check is already broken.

## 3. Audience over capability.

Termin source is reviewable by product managers, security reviewers, compliance officers, and domain experts — not only programmers. We accept a narrower expressive surface in the core language in exchange for a review audience that spans the whole accountability chain.

## 4. Providers over primitives.

The eight primitives are closed because the audit promise depends on them. The provider surface is open because adoption depends on that. Termin extends through new storage backends, identity systems, design systems, and compute providers — never through new primitives.

## 5. Declared agents over ambient agents.

AI participates in Termin applications through typed channels, declared scopes, and audited actions — never as an unbounded caller. The seam between the deterministic zone and the AI zone is structural and runtime-enforced. An agent in a Termin app has a passport, not a backstage pass.

---

## How these are used

Tenets are tiebreakers. When a design question has two reasonable-sounding answers, the tenets declare which direction Termin leans and why. They are durable — they outlast any particular version of the language — and strategic — they operate above implementation decisions rather than specifying them.

Earlier tenets win when they conflict with later ones. In practice, most decisions fire only one or two tenets; cases where multiple tenets push opposite directions are rare and signal a question that deserves deeper design work before resolution.

Extension-within-primitive is open; extension-by-new-primitive is closed. New field types, new Compute shapes, new provider contracts — these extend a closed primitive. New primitives alongside the existing eight are not part of the extensibility story.

---

*Project governance — stewardship, conformance authority, contribution process — inherits from these tenets but is documented separately.*
