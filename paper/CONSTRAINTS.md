# NOMS 2027 paper — constraints (decided; do not re-ask)

- **Venue:** IEEE/IFIP NOMS 2027, full paper. Montreal, 10–14 May 2027.
  Theme: "Intent, Agents, and Autonomy: Shaping the Future of Network Management".
- **Deadline:** 30 Nov 2026 (firm). Plan backwards:
  - claims approved by **16 Nov**
  - full draft by **22 Nov**
  - audit and compile by **27 Nov**
  - submit by **29 Nov**
- **Format:** `\documentclass[conference]{IEEEtran}`, two-column, one PDF.
- **Length:** 8 pages main text, self-contained (reviewers need not read beyond it).
  References + optional appendix may bring the total to at most 12 pages.
  Appendix holds reproducibility details, extra tables, pseudo-code, proofs.
- **Originality:** not published or under review elsewhere. Builds on the
  author's SATNAC 2026 paper; the text must state exactly what is new.
- **Topics to name in the introduction where they truly apply:** wireless and
  cellular networks; 5G and beyond; mission-critical networks; proactive
  management; autonomic and cognitive management; AI-based management;
  monitoring and measurements; testbed experimentation.
- **Review mode:** NOT double-blind (author confirmed 2026-11-12). Author names go in.
- **Artifact Evaluation:** voluntary, after acceptance (badges). Keep the repo
  ready for it.

## Working rules
- Never invent a number, citation, result or fact. Every figure, table and claim
  traces to `logs/`, `scripts/` or `docs/`. Missing → `[TODO]` and ask.
- Cite only references the author gives or that are verified by web search;
  unverified ones are marked `[VERIFY]` in `paper/refs.bib`.
- Negative and null results are allowed and stated plainly. A CI that includes
  zero means the effect is not established.
- A result is suspect until it survives its control (null control, held-out
  terrain, fresh seeds, dead-process censoring, and — added from this
  project's history — uplink-congestion removal for forest labels).
- LaTeX under `paper/`; each figure/table's script sits next to its output.
- Work step by step; stop after each step for approval.
