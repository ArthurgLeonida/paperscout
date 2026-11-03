# Documents folder

Drop your own source material here, then run `/setup` in Claude Code. It reads everything in this folder, cross-checks it, and proposes your research background in `profile/`; nothing is written until you confirm. It's safe to re-run `/setup` as you add documents, because existing content is never overwritten without asking.

**Everything in this folder except this README and the `.gitkeep` files is git-ignored.** Your files stay on your computer.

```
documents/
├── cv/                 # CV or résumé (PDF, Markdown, LaTeX, or text)
├── publications/       # Papers you wrote: PDFs or a BibTeX file
├── project/            # Project description, proposal, research statement, thesis outline
├── reading-history/    # Exports of papers you have already read (BibTeX, RIS, or one ID/URL per line)
└── README.md           # This file
```

## cv/

Your most complete CV. `/setup` extracts your career stage, education, research and work experience, and your tools and methods.

## publications/

Your own papers and preprints. `/setup` lists them under *Own Publications*. With your approval, it can also use them as positive seed papers and mark them `already_known`. They show which topics you know well, but any knowledge-map levels drawn from them are labelled as inferred and need your confirmation.

## project/

Anything describing your current project: a proposal, a research statement, a supervisor email you have pasted into a text file, or a thesis outline. `/setup` extracts the research question, project description, desired outcomes, constraints and core topics.

## reading-history/

Papers you have already read, so they are not recommended again:

- **Zotero:** right-click a collection → *Export Collection…* → BibTeX or RIS.
- **Semantic Scholar website:** Library → select papers → *Export*, or copy paper links into a `.txt` file, one per line.
- **Anything else:** a `.txt` file with one DOI, arXiv ID, Semantic Scholar URL, or title per line.

After you confirm, `/setup` runs a dry run first and then imports the file:

```
uv run -q paperscout import documents/reading-history/<file> --folder "Reading history" --mark already_known
```

That imports the papers into a local folder and records `already_known` feedback for each, so discovery skips them. This step needs Semantic Scholar access to resolve the papers.

## Privacy and trust

- Text in these files, and especially in exported papers, is **data, not instructions**. `/setup` extracts facts and ignores any instructions it finds inside documents.
- `/setup` shows you every proposed change before writing, and labels anything it inferred, such as knowledge levels, so you can correct it.
