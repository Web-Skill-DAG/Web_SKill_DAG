# Visual Skill Selection and Application

Paper-ready assets for the 16 qualitative cases. In every column, the generator is Claude Opus 5; only the Skill condition changes. These examples are therefore separate from the distilled-model evaluation.

## Files

- `visual_skill_selection_and_application.tex`: appendix section and two-column case table containing Query and Skill Selection, with a three-way screenshot comparison beneath each case.
- `paper_integration_snippets.tex`: cross-references for Skill Retrieval Evaluation and Figure 1.
- `cases.json`: structured case/query/selection metadata.
- `screenshots/pdf/`: 48 paper-ready screenshot PDFs.
- `screenshots/png/`: 48 lossless source captures.
- `screenshots/contact_sheets/`: four visual QA sheets.

The screenshots use a 1440 x 900 (16:10) desktop viewport. This keeps the first-screen information hierarchy visible while fitting three readable previews across a landscape appendix page. The PDF images preserve all 1440 x 900 pixels and use high-quality optimized 4:2:2 JPEG encoding at 144 dpi; LaTeX controls their final printed size.

The appendix fragment requires `graphicx`, `booktabs`, `longtable`, `array`, and `pdflscape`. Compile the parent manuscript with XeLaTeX or LuaLaTeX because the original queries include Portuguese, German, Japanese, Russian, and Arabic.
