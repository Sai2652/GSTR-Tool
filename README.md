# GSTR-1 Generator

Browser-based tool that converts a sales register (Tally-exported .xls or
.xlsx) into a portal-ready GSTR-1 JSON file plus a verification report.
Features:

- **Single-user login** — password-protected access
- **Multiple firm profiles** — saved between sessions
- **Drag-drop batch upload** — process two firms in one go
- **GSTIN validation & auto-correction** with checksum verification
- **Invoice consolidation** — merges multi-line invoices correctly
- **HSN-wise summary** with document counts
- **B2B / B2CL / B2CS classification** per GSTR-1 schema
- **File management** — manual cleanup of uploads/outputs

---

## Two ways to run it

### Option 1 — Cloud-hosted on Render.com

Live URL accessible from any browser. See **`DEPLOY_RENDER.md`** for the
full step-by-step guide. Short version:

1. Push this repo to GitHub
2. Create a Render account → New Blueprint → connect your repo
3. Set `APP_USERNAME` and `APP_PASSWORD_HASH` env vars
4. Open the URL and sign in

Free tier works but has caveats — see `DEPLOY_RENDER.md`.

### Option 2 — Locally on your laptop

**Windows:** double-click `run.bat`
**Mac/Linux:** `./run.sh`

Open http://127.0.0.1:5050.

For local use the login is bypass-able by editing `web/auth.py` (or just set
`APP_USERNAME=admin` and a password hash via env).

---

## Workflow

### 1. Add your firms (one-time)

Click **Firms → Add firm**. Enter name + GSTIN. Live checksum validation as
you type.

### 2. Generate JSON

Home page → set period → drag-drop each firm's sales sheet onto its card →
click **Generate**. Each firm produces:

- **GSTR-1 JSON** — upload to GST Portal Offline Tool
- **Excel report** — Summary, Invoices, HSN, Document Issued, Exceptions,
  per-bucket details
- **Combined zip** of the whole batch

### 3. Manage files

**Files** page lists everything currently stored. Delete individual files or
clear all. Recommended: clear after each filing cycle to limit data
retention.

---

## What the tool does

### GSTIN validation & auto-correction
- **Checksum** — uses GSTN's official mod-36 algorithm
- **Auto-correct** — fixes common typos (`O`↔`0`, `I`↔`1`, `L`↔`1`) by
  trying substitutions and re-verifying the checksum
- **Master-list fuzzy match** — when substitution fails, fuzzy-matches
  against valid GSTINs in the same file (rapidfuzz, ≥ 87%)
- **Customer-name cross-check** — flags rows where the sheet's customer
  name doesn't match the canonical name for that GSTIN

### Invoice consolidation
- Groups source rows by `(GSTIN, invoice_no, invoice_date)`
- Within each invoice, groups line items by `(HSN, tax_rate)`
- Different HSNs → separate line items (correct GSTR-1 treatment)

### Cross-checks performed
- Invoices outside declared period (warning)
- Customer GSTIN equal to firm GSTIN (self-invoice — warning)
- Missing/invalid GSTINs (Exceptions sheet)

### JSON sections produced
`b2b`, `b2cl`, `b2cs`, `hsn`, `doc_issue`

---

## File structure

```
gstr1_tool/
├── README.md                   # This file
├── DEPLOY_RENDER.md            # Render deployment guide
├── render.yaml                 # Render blueprint
├── requirements.txt            # Python dependencies
├── .gitignore                  # Excludes secrets and client data
├── .env.example                # Template for env vars
├── run.sh / run.bat            # Local launchers
├── scripts/
│   └── hash_password.py        # Generate bcrypt hash for APP_PASSWORD_HASH
├── src/                        # Core processing engine
│   ├── data_reader.py          # Format detection + column normalization
│   ├── gstin_validator.py      # Checksum + auto-correct + fuzzy match
│   ├── validator.py            # Row-level validation pipeline
│   ├── consolidator.py         # Invoice grouping & classification
│   ├── json_builder.py         # GSTR-1 JSON section builders
│   ├── report_builder.py       # Excel report builder
│   └── main.py                 # CLI alternative to web UI
└── web/                        # Web application
    ├── app.py                  # Flask routes + API
    ├── auth.py                 # Login/logout, session management
    ├── firm_store.py           # Firm profile persistence
    ├── period_utils.py         # Period parsing
    ├── file_manager.py         # Upload/output file management
    ├── data/                   # firms.json (gitignored)
    ├── uploads/                # Uploaded sheets (gitignored)
    ├── output/                 # Generated outputs (gitignored)
    ├── templates/
    │   ├── login.html
    │   ├── index.html
    │   ├── firms.html
    │   └── files.html
    └── static/
        ├── styles.css
        ├── app.js
        ├── firms.js
        └── files.js
```

---

## Real GSTIN issues caught in sample data

| Original | Corrected | Source |
|---|---|---|
| `29AAIFI4763LIZR` | `29AAIFI4763L1ZR` | Infini Motors — `I` typed for `1` |
| `29ABICS6984RIZY` | `29ABICS6984R1ZY` | Super Century — same error |
| `29AAAC08088P1ZH` | `29AAACO8088P1ZH` | Online Instrument — `0` typed for `O` |

Each correction is mathematically verified — only candidates whose mod-36
checksum is valid are accepted.

---

## Security notes

- Password stored as bcrypt hash (12 rounds), not plaintext
- HTTPS automatic on Render
- Sessions expire after 8 hours
- File uploads excluded from git via `.gitignore`
- `APP_PASSWORD_HASH` and `FLASK_SECRET_KEY` set only as env vars, never in code

---

## Notes & limitations

- **CDNR / CDNUR / Exports** sections not built (not requested). Easy to
  add following the existing pattern in `json_builder.py`.
- **Reverse charge** is hard-coded to `"N"`. Add a column and conditional
  logic if needed.
- **JSON schema version** is `GST3.2.4`. Verify against the current GSTN
  offline utility before filing.
- **GST portal API verification** is a stub — wiring requires GSP credentials.
