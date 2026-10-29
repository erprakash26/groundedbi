# GroundedBI web UI: design notes

Subject: an analytics assistant whose whole point is that every number is checked.
Audience: recruiters, interviewers and engineers opening a portfolio link.
Primary job: ask a question and immediately see a verified answer and the evidence behind it.

## Concept: the audited ledger
Auditors put a small tick next to every figure they have checked against source documents.
GroundedBI does the same thing in code, so the UI shows it literally:
- verified numbers in the answer carry an auditor's tick (green pencil)
- hovering or focusing a ticked number highlights the ledger cell it came from
- the evidence is a columnar ledger (tabular figures, column rules), the answer is a memo

## Tokens
Color (light): paper #F2F5F0, sheet #FCFDFB, ink #18263B, graphite #56635C, rule #C8D5CA,
audit green #1E7348, query amber #9A6206, correction red #A8332A.
Color (dark): paper #0E1828, sheet #15233A, ink #E4EBE6, graphite #9DB0A6, rule #2A3D57,
green #63C891, amber #E2AA4C, red #F08074.
Type: Source Serif 4 (answer memo, page title) + Schibsted Grotesk (controls, ledger, labels).
Tabular lining figures in the ledger. Sentence case everywhere, no all-caps labels.

## Layout
header (title, one-line purpose, data scope)
ask bar (input + Ask) and example questions
result: memo (left, wider) | ledger (right); stacked on phones
trace: numbered timeline (it really is a sequence of tool calls)
footer: honest notes (rule-based planner, synthetic data, eval result) + links

## Review against defaults
- Not cream + terracotta, not black + acid accent: cool ledger paper, navy ink, green pencil.
- Not identical SaaS cards: the memo and the ledger have different treatments; no drop shadows.
- No eyebrow labels, no middle-dot meta strings, no arrows on buttons, no monospace labels.
- One motion moment: ticks draw in when an answer arrives (off with reduced motion).

## Brand
- Mark: a rising bar chart with a verifying tick on a navy tile (app/web/logo-mark.svg).
  The tick has a navy outline so it stays separate from the bars at small sizes.
- Wordmark: "GroundedBI" in Schibsted Grotesk, weight 750, letter-spacing -0.02em.
- Files: favicon.svg, apple-touch-icon.png (180px, full bleed), og-image.png (1200x630),
  docs/logo.png for the README (one file, readable on GitHub's light and dark themes).

## Pages
- / (Ask): question box, examples, memo + ledger, trace.
- /explore: stat tiles and charts from /analytics. Totals and percentages come from the
  server; the page only formats integer cents and picks which month to highlight.
- /how-it-works: request flow, safety layers, evaluation results, limitations.
