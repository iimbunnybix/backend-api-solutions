# 017 · Merchants re-uploaded the same 1,000-row product file a dozen times because one bad row rejected all of it

## Problem
A marketplace lets merchants upload product catalogues through
`POST /products:bulk-import`. Large merchants upload 1,000 rows at a time from
spreadsheets. Support tickets piled up: "the import failed, nothing was
created". A typical file had a handful of rows with an empty price. Each upload
returned `400 row 812 invalid`, the merchant fixed that one row, uploaded again
and hit the next bad row. Some merchants gave up and listings went live days
late; very large files also held one database transaction open for minutes.

In the simulation in `solution.py`, a 1,000-row upload with one bad row
(index 812) saves **0** rows with the original endpoint and **999** with the
fix.

## Root cause
The endpoint treated the whole file as one unit of work:

- every row was inserted inside **one transaction**, and the first invalid row
  rolled back all of them;
- validation **stopped at the first error**, so the response could only name
  one bad row (and only one problem in that row);
- there was **no limit on batch size**, so one request could hold locks for as
  long as the file took to process.

The rows are independent products, so all-or-nothing semantics were never
required; they were just the easiest thing to write.

## Solution
`bulk_import()`:

1. Rejects batches over `MAX_BATCH_SIZE` (1,000) with **413** before writing
   anything, so clients split large files.
2. Validates each row fully and collects **every** error for that row.
3. Saves each valid row in its **own small transaction**, so a bad or
   duplicate row affects only itself.
4. Returns one result per row (`index`, `sku`, `status` =
   `created` / `invalid` / `duplicate`, `errors`) plus counts. The status code
   is **200** when every row was created and **207 Multi-Status** otherwise,
   so clients know to read the per-row results.

Re-uploading the same file is safe: rows already created come back as
`duplicate` instead of being inserted twice.

## Trade-offs
- Partial success means the catalogue can briefly contain half an upload.
  If rows depend on each other (an order and its lines, a parent product and
  its variants) keep those groups in one transaction and make the *group* the
  unit of partial success.
- One transaction per row is slower than one big transaction. Real systems
  often use savepoints or insert valid rows in chunks of a few hundred.
- Clients must read the per-row results; a client that only checks
  "status < 300" will treat 207 as full success.
- For files much larger than the limit, an asynchronous import job (upload,
  get a job ID, poll for a downloadable error report) is a better interface
  than one synchronous request.

## Run
```bash
python -m unittest -v test_solution.py
```
