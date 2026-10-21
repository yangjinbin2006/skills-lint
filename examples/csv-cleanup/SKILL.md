---
name: csv-cleanup
description: Clean and reshape CSV files. Use when the user asks to deduplicate rows, normalise inconsistent dates or headers, split one CSV into several files, or merge CSVs whose columns do not line up.
version: 1.0.0
---

# CSV cleanup

Bring a messy CSV into a predictable shape before anything else touches it.

## When this applies

The user has a CSV that is almost usable: duplicated rows, mixed date formats,
headers that change between files, or several exports that need to become one.

## Workflow

1. Read the header line and record the exact column names — do not normalise them silently.
2. Report the shape before changing anything: row count, column count, detected duplicates.
3. Apply only the transformation the user asked for. Ask before dropping columns.
4. Write to a new file; never overwrite the input.

## Constraints

- Preserve the original encoding unless the user says otherwise.
- Keep a copy of the input path in the output filename so the change is traceable.
