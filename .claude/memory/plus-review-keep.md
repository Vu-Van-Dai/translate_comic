---
name: plus-review-keep
description: Reviewing ChatGPT Plus answers -- never delete or reject a line Plus translated; only refine wording and fill what both refused
metadata:
  type: feedback
---

User 2026-11-12: when I review the lines ChatGPT Plus translated (PLUS-ANSWERED in plus_fallback.log), I must
NOT remove or blank any of them, even when one goes against my own content guidelines -- Plus translated it
under its own rules, so I don't reject it outright. Allowed: refine the wording (meaning, pronoun pairs,
glossary, consistency), and translate the parts that were left untranslated or refused by BOTH AgentRouter and
Plus (those are mine to do, per [[content-boundaries]]).

**Why:** the user accepts Plus's own policy as sufficient for what it already translated; deleting its work
loses lines and creates holes.
**How to apply:** review = edit wording in place only; never empty a target_text Plus filled. See [[agentrouter-addon]].

**The one exception (user 2026-11-12, discussed twice):** a line that is SEXUAL content involving a minor.
- The page is still CLEANED and still gets its PSD, like every other page (user: "I still need a cleaned-up PSD
  to handle the rest myself"). Only those regions are left as cleaned, empty bubbles -- I don't typeset them.
- Plus's text stays untouched in translation.json (never deleted); I don't refine it. I list chapter/page/region
  to the user, who handles those bubbles.
- Pages whose ARTWORK sexualises a minor are the only thing I don't process at all (existing rule, [[content-boundaries]]).
Non-sexual scenes with minors (school, family, everyday life) are ordinary content: reviewed and kept like any other line.
