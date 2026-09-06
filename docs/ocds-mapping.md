# SEAP → OCDS field mapping

Target: **OCDS 1.1**. Source: `GET /api-pub/PublicDirectAcquisition/getView/{id}`,
merged with the corresponding row from
`POST /api-pub/DirectAcquisitionCommon/GetDirectAcquisitionList/`.

## Why the two responses must be merged

The detail endpoint returns `contractingAuthorityID` and `supplierId` as **numeric IDs
only**. The human-readable `"CUI Name"` strings exist solely on the *list* response.
Using the detail alone silently produces releases with no buyer and no supplier — this
was a real bug, caught by checking for nulls after the first run.

## Release level

| OCDS path | SEAP source | Notes |
|---|---|---|
| `ocid` | `directAcquisitionID` | `{prefix}-da-{id}`. Prefix is provisional until OCP assigns one. |
| `id` | `directAcquisitionID` + `finalizationDate` | Unique per release |
| `date` | `publicationDate` | ISO 8601 with offset |
| `tag` | — | `["tender","award"]`: a direct acquisition has no separate tender stage |
| `initiationType` | — | Always `"tender"` (OCDS requires it) |
| `language` | — | `"ro"` |
| `buyer` | `contractingAuthority` *(list)* | Split into CUI + name |
| `parties[]` | `contractingAuthority`, `supplier` | Roles `buyer` / `supplier` |

### Organisation identifiers

`split_org()` parses `"CUI Name"`. Four shapes occur:

```
"9626572 - FUNDATIA DE SPRIJIN COMUNITAR"
"33203265 Expert Business Center SRL"
"RO 15437993 ROMSYSTEMS"      <- VAT prefix, with space
"RO15169122 MEDISERV"         <- VAT prefix, no space
```

The `RO` prefix is a **VAT marker, not part of the CUI**. Failing to strip it dropped the
identifier for 57 of 72 suppliers in a sample run — destroying the join key for all
supplier-level analysis. Covered by regression tests in `tests/test_ocds.py`.

Identifiers are emitted as `{"scheme": "RO-CUI", "id": "<digits>"}`.

## Tender

| OCDS path | SEAP source |
|---|---|
| `tender.id` | `uniqueIdentificationCode` (e.g. `DA41081708`) |
| `tender.title` | `directAcquisitionName` |
| `tender.description` | `directAcquisitionDescription` |
| `tender.value.amount` | `estimatedValue` |
| `tender.procurementMethod` | `"direct"` |
| `tender.mainProcurementCategory` | `sysAcquisitionContractType.text` → `goods` / `services` / `works` |
| `tender.classification` | `cpvCode` (header) |
| `tender.items[]` | `directAcquisitionItems[]` |

## Award

| OCDS path | SEAP source |
|---|---|
| `awards[0].id` | `daAwardNoticeID` |
| `awards[0].status` | `sysDirectAcquisitionState.text` — `active` when it contains "acceptat" |
| `awards[0].date` | `finalizationDate` |
| `awards[0].value.amount` | `closingValue` |
| `awards[0].suppliers[]` | `supplier` *(list)* |

## Items — the important part

| OCDS path | SEAP source | Notes |
|---|---|---|
| `items[].id` | `directAcquisitionItemID` | |
| `items[].description` | `catalogItemName` | |
| `items[].x_longDescription` | `catalogItemDescription` | Non-standard; carries specification text |
| `items[].quantity` | `itemQuantity` | |
| `items[].unit.name` | `itemMeasureUnit` | Free text, unnormalised at this layer |
| **`items[].unit.value.amount`** | **`itemClosingPrice`** | **Price of ONE unit** |
| `items[].classification` | `cpvCode` (object) | Code read from `localeKey` |

### The unit-price finding

`itemClosingPrice` is the **unit** price, not the line total. This was established
empirically, not assumed, because getting it wrong corrupts every downstream figure and
quantity is 1 in most records — which hides the distinction.

Evidence from live data:

| Item | qty | `itemClosingPrice` | Reading as unit price | Reading as total |
|---|---|---|---|---|
| Laptop Lenovo V15 G4 | 15 | 3637.00 | 3637 RON/laptop ✓ | 242 RON/laptop ✗ |
| Apă minerală 2 L | 384 | 1.75 | 1.75 RON/sticlă ✓ | 0.005 RON ✗ |
| Servicii de pază cu agent | 680 ore | 34.00 | 34 RON/oră ✓ | 0.05 RON/oră ✗ |
| UPS APC BX750MI 750VA | 4 | 406.00 | 406 RON/UPS ✓ | 101 RON ✗ |

Confirmed by the header invariant, which held for **25/25** sampled records within 2%:

```
closingValue == Σ (itemClosingPrice × itemQuantity)
```

`tests/test_ocds.py::TestRelease::test_header_total_equals_sum_of_line_totals` guards it.

## Enrichment: county

Not present on either procurement response. Resolved via
`GET /api-pub/Entity/getCAEntityView/{contractingAuthorityID}`, which returns `county`,
`city` and `fiscalNumber`. Results are cached per authority — authorities repeat heavily,
so this costs far less than one request per acquisition.

That response **also** returns `email`, `phone` and `fax`, which the scrubber removes.

## Not yet mapped

`planning`, `contracts`, `milestones`, `amendments`. Contract amendments are available
from data.gov.ro (`date din modificare contract`) and are a Stage 1 target — notably,
these are exactly the stages left **empty** in the existing OpenTender publication.
