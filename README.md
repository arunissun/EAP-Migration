# IFRC GO EAP migration — simple user guide

This tool helps a GO user:

- create a new Simplified or Full EAP draft in the GO staging environment; and
- update an existing EAP that is still **Under Development** and unlocked.

The tool stops at **Under Development**. It does not submit, approve, revise,
share, sign agreements, activate, or delete EAPs.

## Important message about API access

Having an API token proves who the user is. It does **not** automatically give
the user permission to make every API request.

The GO user also needs the relevant GO permissions for:

- `POST` requests, such as uploading files and creating a new EAP; and
- `PATCH` requests, such as updating an existing EAP.

A token may successfully authenticate a user while the API still returns
`403 Forbidden` because the user does not have the required permission. Ask a
GO administrator to grant the correct permission. Do not try to solve a
permission problem by changing or sharing tokens.

## 1. Set up the tool

Run these commands from the repository folder:

```powershell
Copy-Item .env.example .env
uv python install 3.11.13
uv venv --python 3.11.13
uv sync --all-groups
```

Put the token value in `.env`:

```text
GO_EAP_API_TOKEN=the-token-value-only
GO_EAP_CONTACT_EMAIL=approved-test-email@example.org
GO_EAP_CONTACT_PHONE=approved-test-phone-number
```

Do not add `Token ` before the token. The tool adds that part itself. Never put
the token in a case JSON file.

## Repository folders

Here is what the main folders contain:

- `cases/`: JSON files describing new Simplified or Full EAP migrations.
- `fixtures/`: sample local attachments used by the Fiji and synthetic Full EAP
  examples and tests.
- `schemas/`: dated OpenAPI and EAP reference-catalog snapshots used for
  contract and validation checks.
- `scripts/`: setup, environment-check, fixture-generation, and OpenAPI-fetch
  scripts.
- `src/`: the migration application code, including the CLI, API client, models,
  validation, recovery, update, and verification logic.
- `tests/`: automated tests for the migration workflows and safety rules.
- `updates/`: small JSON change documents for updating existing EAP drafts.
- `.state/` and `artifacts/`: generated local progress, receipts, and update
  plans; these are ignored by Git.

Markdown documentation files and local secrets are also ignored by Git.

## 2. Prepare a JSON file for a new EAP

The new-EAP JSON file contains four important parts:

1. `eap_kind`: `simplified` or `full`;
2. a unique `migration_key`;
3. `registration`: country, National Society, disaster, contacts, and EAP type;
4. `files` and `application`: the EAP information and references to local files.

The registration type must match the EAP kind:

- Simplified EAP: `eap_type: 20`;
- Full EAP: `eap_type: 10`.

Start from an existing case to keep the correct field names:

```powershell
Copy-Item cases/fiji-simplified-eap.json cases/my-new-eap.json
```

Then replace the Fiji data with the real reviewed data. Use the contact
placeholder instead of putting a reusable personal email directly in the file:

```json
"national_society_contact_email": "${GO_EAP_CONTACT_EMAIL}"
```

Use `${GO_EAP_CONTACT_PHONE}` in a case file when an approved test phone number
is required. Do not copy personal contact details from a source PDF into staging.

A file is declared once in `files` and referenced by its logical name in the
application:

```json
"files": {
  "cover": {
    "path": "attachments/cover.png",
    "caption": "EAP cover image",
    "kind": "cover"
  },
  "budget": {
    "path": "attachments/budget.xlsx",
    "caption": "EAP budget",
    "kind": "budget"
  }
},
"application": {
  "cover_image_file": "cover",
  "budget_file": "budget"
}
```

The example above is only a small illustration. Use the complete structure in
the existing case file for the selected EAP kind. Do not invent narrative text
just to fill a field. The default `completeness_profile` is `draft`, so missing
readiness content is reported as a warning. Use `"strict"` when unfinished
content must stop the migration.

## 3. Migrate a new EAP

Run the commands in this order:

```powershell
uv run eap-migrate validate cases/my-new-eap.json
uv run eap-migrate plan cases/my-new-eap.json --environment stage
uv run eap-migrate apply cases/my-new-eap.json --environment stage --confirm-stage-writes
uv run eap-migrate verify cases/my-new-eap.json --environment stage
```

What the commands mean:

- `validate` checks the JSON and local files. It does not contact GO.
- `plan` reads GO and shows what would happen. It does not create anything.
- `apply` uploads referenced files and creates the registration and EAP draft.
- `verify` reads the new records again and checks their fields, files, status,
  version, and lock state.

Review the `plan` output before using `apply`. Stop if there are conflicts,
invalid reference values, or unexpected existing records.

State is stored in repository-root `.state/`. Receipts and update plans are
stored in repository-root `artifacts/`. The old `cases/.state/` location is
treated as legacy and is never moved automatically.

## 4. Update an existing EAP

Updates are allowed only when:

- the target EAP is the intended application;
- its registration has numeric status `10` (**Under Development**);
- its `is_locked` value is `false`; and
- the user has permission to make PATCH requests.

The user does not need to provide the entire original EAP JSON. Create a small
change file containing only the fields to change:

```json
{
  "changes": {
    "trigger_statement": {
      "set": "Activate when the revised forecast threshold is reached."
    },
    "planned_operations": {
      "add": [
        {
          "sector": 101,
          "people_targeted": 2000,
          "budget_per_sector": 1000,
          "indicators": [],
          "readiness_activities": [],
          "prepositioning_activities": [],
          "early_action_activities": []
        }
      ]
    }
  }
}
```

Use the operations as follows:

- `set`: replace a complete narrative or scalar value;
- `add`: add a new list item;
- `remove`: remove an existing list item using its stable `id` or matching key;
- `update`: change selected properties of one existing list item;
- `replace`: deliberately replace an entire list.

Do not remove a list item by its position, such as “item 2”. For a narrative,
provide the complete replacement text. The tool never silently truncates it.

Prepare a GET-only update plan:

```powershell
uv run eap-migrate update-plan updates/my-eap-changes.json `
  --application-id 23 `
  --eap-kind simplified `
  --output artifacts/my-eap-update-plan.json
```

The plan contains two JSON payloads:

- `final_payload`: the complete expected EAP after the changes, used for review
  and verification;
- `patch_payload`: only the changed fields, used for the actual PATCH.

For a changed list, `patch_payload` contains the complete resulting list because
the API replaces the whole list when that list is patched. Unchanged fields are
not sent.

After reviewing the plan, apply it once:

```powershell
uv run eap-migrate update-apply artifacts/my-eap-update-plan.json `
  --environment stage --confirm-stage-writes
uv run eap-migrate update-verify artifacts/my-eap-update-plan.json `
  --environment stage
```

The update uses `PATCH` and the current `modified_at` value. If someone else
changed the EAP after the plan was prepared, the timestamp will be different
and the tool will stop before PATCH. Re-run the plan after reviewing the new
remote content.

Removing a file from an EAP only removes its reference from the EAP. It does not
delete the uploaded file from GO.

## 5. Migrate several new EAPs

Put only the intended case files in a separate folder, then run:

```powershell
uv run eap-migrate batch batch-cases --environment stage
```

This validates and plans every case before any write. A duplicate migration key,
duplicate registration, or unsafe case stops the batch with zero writes.

Only after reviewing the complete batch plan:

```powershell
uv run eap-migrate batch batch-cases --environment stage `
  --apply --confirm-stage-writes
```

Cases are applied one at a time. If an unexpected remote change interrupts the
batch, the report lists completed and unattempted cases.

## 6. Common errors

| Error | Simple meaning | What to do |
|---|---|---|
| `Case validation failed` | The JSON has a missing, invalid, or unknown field. | Read the field named in the error and correct the case JSON. |
| `Required local file does not exist` | A referenced attachment cannot be found. | Correct the file path or add the missing file. |
| `unsupported extension` | The file type is not allowed for that field. | Use a supported document/image type. Covers must be raster images; budgets/checklists must use the restricted document types. |
| `SVG files are not supported` | SVG files are blocked. | Use PNG or JPEG for an image. |
| `maximum is 100 MB` | A file is too large. | Reduce the file size or review the configured local limit. |
| `caption ... 225-character limit` | A file caption is too long. | Shorten the caption. |
| `maximum is 5` | A supporting-files section has too many files. | Keep no more than five files in that section. |
| `Reference validation failed` | A country, National Society, disaster, partner, or user ID was not confirmed by GO. | Check the ID and run `plan` again. |
| `catalog` or `timeframe` error | A sector, approach, unit, or value is not in the current GO catalog. | Correct the value; do not guess a code. |
| `OpenAPI drift` | The live API contract differs from the saved/local contract. | Stop and have the schema difference reviewed before writing. |
| `conflicts` in the plan | GO already contains a matching or ambiguous record, or the state is unsafe. | Stop and inspect the remote record. Do not apply blindly. |
| `recovery ... ambiguous` | A POST or PATCH may have reached GO, but the client did not receive a safe result. | Inspect GO and the state/plan. Do not immediately retry. |
| `stale state` or `record no longer exists` | Local state points to a missing remote record. | Confirm what happened in GO before resetting only that case’s state. |
| `Case content changed since ...` | The case JSON differs from the file used for saved progress. | Review the remote record before using an explicit state reset. |
| `401 Unauthorized` | The token is missing, expired, or invalid. | Check `.env` and the token value. |
| `403 Forbidden` | The token is valid, but the GO user lacks permission for that request. | Ask a GO administrator for the required POST or PATCH permission. |
| `404 Not Found` | The requested record or reference does not exist at that URL. | Check the ID, environment, and saved state. |
| `409 Conflict` | The target changed, is locked, or cannot accept the requested update. | Re-read the EAP, review the difference, and prepare a new plan. |
| `429 Too Many Requests` | GO is rate-limiting requests. | GET requests retry automatically within a limit; wait if necessary. |
| `500`, `502`, `503`, or `504` | GO or a gateway returned a temporary server error. | GET requests retry automatically. Review before retrying any write. |
| `GET request failed` | The client could not complete a read request. | It may retry automatically; check connectivity if it continues. |
| `POST/PATCH outcome is ambiguous` | The client cannot tell whether the write reached GO. | Never blindly repeat the write. Inspect GO first. |
| `Application verification failed` | A returned field, list item, file, link, version, or status differs from the expected result. | Read the exact field path. For updates, compare it with the reviewed final payload. |
| Windows `PermissionError` while saving state | Windows briefly locked the local state file. | The tool retries briefly. If it continues, close other programs using the repository and retry after review. |

For local progress, inspect one case with:

```powershell
uv run eap-migrate state show --case my-migration-key
```

If the case uses the old local state folder, inspect it explicitly:

```powershell
uv run eap-migrate state show --case fiji-cyclone-seap-2026 `
  --case-path cases/fiji-simplified-eap.json `
  --state-root cases/.state
```

Do not use `state reset` merely because a command failed. Reset state only after
reviewing the corresponding remote record.

## Safety reminders

- The tool is staging-only: `https://goadmin-stage.ifrc.org`.
- `validate`, `plan`, `verify`, `update-plan`, and `update-verify` do not change
  EAP records.
- `apply` and `update-apply` are the only state-changing commands and require
  explicit confirmation.
- Never run `apply` again just because a POST may have succeeded.
- Never call or add status, revise, approval, agreement, sharing, activation, or
  deletion workflows to this migration tool.
