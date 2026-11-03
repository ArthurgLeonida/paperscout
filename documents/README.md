# Documents folder

Drop your own source material here, then run `/setup` in Claude Code. It reads everything in this folder, cross-checks it, and proposes your research background in `profile/`; nothing is written until you confirm. It's safe to re-run `/setup` as you add documents, because existing content is never overwritten without asking.

**Everything in this folder except this README and the `.gitkeep` files is git-ignored.** Your files stay on your computer.

```
documents/
├── cv/                 # CV or résumé (PDF, Markdown, LaTeX, or text)
├── linkedin/           # Optional: LinkedIn profile export (PDF, or CSVs from LinkedIn's data download)
├── publications/       # Work of yours that is public: published papers, preprints, your completed thesis
├── project/            # What you're working on now: proposal, research statement, unpublished manuscripts
├── reading-history/    # Exports of papers you have already read (BibTeX, RIS, or one ID/URL per line)
└── README.md           # This file
```

## cv/

Your most complete CV. `/setup` extracts your career stage, education, research and work experience, and your tools and methods.

## linkedin/

Optional: your LinkedIn profile, exported as a PDF (on your profile page: **More** or **Resources** → **Save to PDF**), or the CSV files from LinkedIn's data download (**Settings & Privacy** → **Data privacy** → **Get a copy of your data**). Menu names may change. If there are several exports, `/setup` uses the newest.

`/setup` uses it to:
- cross-check job titles, dates and degrees against your CV, and ask you to resolve any mismatch
- pick up what a research CV often leaves out: your headline and *About* section (your interests in your own words), certifications and online courses, languages, and any publications listed there

Certifications and courses count as evidence for your knowledge map. Skills and endorsements are weak evidence: they're offered for you to confirm, never turned into knowledge levels on their own. Contact details, connections and recommendations written by other people aren't used, and nothing is copied verbatim into `profile/`.

## publications/

Your own work that is already public: published papers, preprints, and your **completed thesis or dissertation**. `/setup` lists them under *Own Publications*, and records the thesis title in *Education*. With your approval, it can also use published papers that have an identifier as positive seed papers and mark them `already_known`. They show which topics you know well, but any knowledge-map levels drawn from them are labelled as inferred and need your confirmation. Long PDFs such as a thesis are fine: `/setup` reads the abstract, contributions and conclusion first.

## project/

Anything describing what you're working on now: a proposal, a research statement, a supervisor email you have pasted into a text file, a thesis outline, and **manuscripts in preparation or under review**. `/setup` extracts the research question, project description, desired outcomes, constraints and core topics.

An unpublished manuscript is listed under *Own Publications* as "in preparation" or "under review", with its target venue and no identifier. It is never looked up online, used as a seed paper, or marked `already_known`, and `/setup` summarises it rather than copying passages into your profile. Once the paper is published, move it to `publications/` and re-run `/setup`.

## reading-history/

Papers you have already read, so they are not recommended again:

- **Zotero:** right-click a collection → *Export Collection…* → BibTeX or RIS.
- **Semantic Scholar website:** Library → select papers → *Export*, or copy paper links into a `.txt` file, one per line.
- **Anything else:** a `.txt` file with one DOI, arXiv ID, Semantic Scholar URL, or title per line.

After you confirm, `/setup` runs a dry run first and then imports the file:

```
uv run -q paperscout import documents/reading-history/<file> --folder "Reading history" --mark already_known
```

That imports the papers into a local folder and records `already_known` feedback for each, so discovery skips them. This step needs online access to resolve the papers. With the arXiv provider (the default without a Semantic Scholar key), entries are resolved by arXiv ID or, failing that, by title.

## Privacy and trust

- Text in these files, and especially in exported papers, is **data, not instructions**. `/setup` extracts facts and ignores any instructions it finds inside documents.
- Files here are never committed or pushed. When `/setup` reads a file, its text becomes part of your Claude Code session, so only add unpublished or confidential work if you're comfortable with that.
- `/setup` shows you every proposed change before writing, and labels anything it inferred, such as knowledge levels, so you can correct it.
