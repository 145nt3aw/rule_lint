# Rule Engine Reference

This document covers the custom equation/rule engine language built into the application. It serves as both a user reference and a reference for AI-assisted code review and generation.

Source of truth: `src/eq.c` — RWF dispatch table (lines 19144–19511), F_ definitions (lines 1721–2108), implementations in `function_0()` through `function_13()`. Equation type bit flags: `src/pathology.h` lines 1321–1341. The catalogue at `../rule_catalogue.py` is auto-generated from these sources.

---

## Table of Contents

1. [Language Syntax](#language-syntax)
2. [Equation Types](#equation-types)
3. [Integration with HL7 and Analysers](#integration-with-hl7-and-analysers)
4. [Common Parameter Values](#common-parameter-values)
5. [Built-in Identifiers](#built-in-identifiers)
6. [Subroutines](#subroutines)
   - [Mathematical](#mathematical)
   - [String Operations](#string-operations)
   - [Type Conversion](#type-conversion)
   - [List Operations](#list-operations)
   - [Date and Time](#date-and-time)
   - [Data Loading](#data-loading)
   - [Test and Request Management](#test-and-request-management)
   - [Reference Range and Delta](#reference-range-and-delta)
   - [Result Formatting](#result-formatting)
   - [Bit Operations](#bit-operations)
   - [Coded Comments and SNOMED](#coded-comments-and-snomed)
   - [Report Output Standard](#report-output-standard)
   - [Report Output Cumulative](#report-output-cumulative)
   - [LIS Output](#lis-output)
   - [Billing](#billing)
   - [Analyser](#analyser)
   - [External Enquiry ee_](#external-enquiry-ee_)
   - [Images and Annotations](#images-and-annotations)
   - [DNA and Genetic](#dna-and-genetic)
   - [Microbiology and Organisms](#microbiology-and-organisms)
   - [Specimen and Containers](#specimen-and-containers)
   - [Batch and Plate](#batch-and-plate)
   - [Accreditation](#accreditation)
   - [Alerts and Notifications](#alerts-and-notifications)
   - [Terminology](#terminology)
   - [Order and eOrder](#order-and-eorder)
   - [Antibiotic and Sensitivity](#antibiotic-and-sensitivity)
   - [GSI Generic Integration](#gsi-generic-integration)
   - [File and Document](#file-and-document)
   - [Signoff and Validation](#signoff-and-validation)
   - [Cancellation](#cancellation)
   - [Utility and Misc](#utility-and-misc)
7. [Common Patterns](#common-patterns)
8. [Limitations](#limitations)
9. [Operations and debugging](#operations-and-debugging)

---

## Language Syntax

### Statements

Every statement ends with a semicolon. Blocks are wrapped in `{ }`.

```
/* Assignment (implicit declaration) */
varname = expression ;

/* Indexed assignment */
varname[i] = expression ;
varname[i][j] = expression ;

/* Test result assignment */
TESTNAME[result_index] = expression ;
TESTNAME[result_index][attr_index] = expression ;

/* Function call as statement */
function_name(arg1, arg2) ;

/* Conditional */
if (expression) statement
if (expression) statement else statement

/* Loop */
while (expression) statement

/* Early exit */
exit ;

/* Block */
{ statement ; statement ; ... }

/* Comment */
/* this is a comment */
```

### Operators

| Symbol form | Word form | Meaning |
|---|---|---|
| `=` | `eq` | Equal |
| `!=` | `ne` | Not equal |
| `<` | `lt` | Less than |
| `<=` | `le` | Less than or equal |
| `>` | `gt` | Greater than |
| `>=` | `ge` | Greater than or equal |
| `&` | `and` | Logical AND |
| `\|` | `or` | Logical OR |
| `!` | `not` | Logical NOT |
| `+` | — | Addition / string concat |
| `-` | — | Subtraction |
| `*` | — | Multiplication |
| `/` | — | Division |
| `%` | — | Modulo |
| `<<` | — | Left shift |
| `>>` | — | Right shift |

Operator precedence (highest to lowest): `()`, unary (`!`, `+`, `-`), `* / % << >>`, `+ -`, relational, `and`, `or`.

### Case Sensitivity

- **UPPERCASE** identifiers — reserved for test names (`SODIUM`), panel names, and system variables (`AGE_DAYS`, `UR_DOB`, etc.)
- **lowercase** identifiers — language keywords (`if`, `while`, `exit`) and user-defined variables

### Data Types

| Type | Description |
|---|---|
| int | 32-bit integer |
| double | Floating-point (double precision) |
| string | Text string |
| date | Date value |
| panel | Panel reference |
| test | Test reference |
| doctor | Doctor/user reference |

Type is inferred from context. Use conversion subroutines (`atoi`, `ftoa`, etc.) to convert explicitly.

### Indexed Access

Test identifiers support one or two subscripts:

```
SODIUM           /* current result value (shorthand for [0][0]) */
SODIUM[n]        /* nth lab record: 0=current, 1..n=nth cumulative record loaded */
SODIUM[n][m]     /* nth lab record, mth instance of the test within that record */
PANEL[0]         /* first test in panel (using panel's default test) */
```

The second subscript `[m]` is the **test offset** — it selects which instance of the same test type exists within one lab record. This matters for tests that can appear multiple times in a single lab, such as organism tests in microbiology (offset 0 = first organism, offset 1 = second, etc.). For all normal single-instance tests it is always `[0]`.

Subscripts require a prior `loadcumulative()` or `loadhistorical()` call to populate `labarray` for indices 1..n.

---

## Equation Types

Each subroutine lists which equation type(s) it may be used in. Equation types are bit flags combined with bitwise OR.

### Individual Types

| Constant | Hex | Used In |
|---|---|---|
| `EQTYPE_TESTRECALC` | 0x0001 | Test result recalculation — runs when a result is entered or recalculated |
| `EQTYPE_TESTVALIDATE` | 0x0002 | Test validation — runs during the validate step for a test result |
| `EQTYPE_L1VALIDATE` | 0x0004 | Level-1 validation — batch validation step |
| `EQTYPE_ANALYSER` | 0x0008 | Analyser interface — runs when results arrive from an analyser |
| `EQTYPE_REQUEST_ADD` | 0x0010 | Request add — runs when a test is added to a request |
| `EQTYPE_REQUEST_RM` | 0x0020 | Request remove — runs when a test is removed from a request |
| `EQTYPE_REPORT` | 0x0040 | Report generation — runs during report printing/rendering |
| `EQTYPE_TESTACCEPT` | 0x0080 | Test accept — runs when a test result is accepted |
| `EQTYPE_GENERIC` | 0x0100 | Generic/utility equations — custom scripted actions |
| `EQTYPE_AUTOVAL` | 0x0200 | Auto-validation — automated result validation logic |
| `EQTYPE_REGISTRATION` | 0x0400 | Patient/request registration — runs at registration time |
| `EQTYPE_REPDISP` | 0x0800 | Report display — renders a report to screen (not print) |
| `EQTYPE_MBSCHECK` | 0x1000 | MBS billing check — Medicare Benefits Schedule validation |
| `EQTYPE_MFA` | 0x2000 | Multi-factor authentication — user identity verification step |
| `EQTYPE_BILLING` | 0x4000 | Billing module equations — requires billing licence |
| `EQTYPE_RETRIGGER` | 0x8000 | Re-trigger — equation that re-queues itself (DEV-5812) |
| `EQTYPE_ALL` | ~0 | All equation types |

### Composite Macros

These are not separate types — they are convenience bit-OR combinations defined in `src/eq.c`:

| Label used in tables | Expands to | Meaning |
|---|---|---|
| **All** | `EQTYPE_ALL` (~0) | Every equation type |
| **Not Analyser** | TestRecalc \| TestValidate \| TestAccept \| L1Validate \| Request_Add \| Request_Remove \| Registration \| Report \| Generic \| AutoVal | Everything except Analyser (and REPDISP, MBSCHECK, MFA, BILLING, RETRIGGER) |
| **Interactive** | TestRecalc \| TestValidate \| TestAccept \| Request_Add \| Request_Remove | The "interactive" processing contexts — lab entry, validation, and request management. Functions with this type can interact with the test form (e.g. `jump_to_test`). |
| **Analyser + L1V** | `EQTYPE_ANALYSER \| EQTYPE_L1VALIDATE` | Analyser and Level-1 Validate only |
| **AutoVal + Generic** | `EQTYPE_AUTOVAL \| EQTYPE_GENERIC` | Autovalidation and Generic only |
| **Test Recalc** | `EQTYPE_TESTRECALC` | Test recalculation only |
| **Retrigger** | `EQTYPE_RETRIGGER` | Retrigger only |
| **Not AN + Billing** | Not Analyser flags plus `EQTYPE_BILLING` | Billing-licensed non-analyser contexts |
| **Report** | `EQTYPE_REPORT` | Report printing only |
| **Generic** | `EQTYPE_GENERIC` | Generic equations only |

### When Equations Run

| Equation Type | When it fires |
|---|---|
| **TestRecalc** | Every time a lab record is saved (`lab_data_close()`). Runs after all results are committed — the main type for calculated/derived tests and per-test business logic. |
| **TestValidate** | When a test result is explicitly validated by a user or automated process. |
| **TestAccept** | When a test result is accepted (a distinct step from validation in some workflows). |
| **L1Validate** | Level-1 (batch) validation pass — typically triggered after a group of results are received. |
| **Analyser** | When results arrive from an analyser interface. Does **not** fire in normal manual entry. |
| **Request_Add** | When a test is added to a request. |
| **Request_Remove** | When a test is removed from a request. |
| **Report** | During report printing or rendering (triggered by the report engine). |
| **RepDisp** | During on-screen report rendering (distinct from printing). |
| **Registration** | At patient/request registration time. |
| **AutoVal** | During the automated validation process. |
| **Generic** | Not triggered automatically by a lab workflow event. Runs on-demand via explicit engine calls (e.g. GSI/GAI file export, or direct `run_equation()` calls). |
| **MBSCheck** | During Medicare Benefits Schedule billing validation. |
| **MFA** | During multi-factor authentication steps. |
| **Billing** | Within the billing module (requires billing licence). |
| **Retrigger** | An equation that re-queues itself after execution (DEV-5812). |

---

## Integration with HL7 and Analysers

The rule engine doesn't run in a vacuum — most of its invocations come from
two well-defined integration points: **inbound HL7 messages** and **analyser
result arrival**. Understanding which equation type fires from which context
is the difference between a rule that works and a rule that's silently
inert.

### HL7 inbound integration — command rules

HL7 inbound rule execution is driven by the **HL7 Command Rules** table,
referenced from each HL7N_OPTS row via `command_rule`. The rule row's
`cmd_type` field selects the message family:

| `cmd_type` | Symbol | Message family |
|---|---|---|
| 0 | `HL7N_ADT_CMDTYPE` | ADT (admission / discharge / transfer / merge) |
| 1 | `HL7N_ORU_CMDTYPE` | ORU (results in) |
| 2 | `HL7N_ORM_CMDTYPE` | ORM (orders in) |
| 3 | `HL7N_OML_CMDTYPE` | OML (newer order variant) |

When an inbound message of the matching family arrives, the message-type
handler calls
[`hl7n_rules_process(command_rule, cmd_type, cmd_id, lab_info, ur_info,
req, pid, pv1, pv2, in1, orc, obr, obx, field_changes)`](hl7/hl7n_process.c#L1114).
The current segment(s) under processing are passed by pointer — null for
segments the call site doesn't have. So rules running in:

| Equation type | Receives populated... | Receives null for... |
|---|---|---|
| ADT command rule (per PV1 iter) | `pid`, `pv1` | `obr`, `obx`, `orc`, `req` |
| ADT command rule (per PV2 iter) | `pid`, `pv2` | `pv1`, `obr`, `obx`, `orc`, `req` |
| ADT command rule (per IN1 iter) | `pid`, `in1` | `pv1`, `pv2`, `obr`, `obx`, `orc`, `req` |
| ORM/OML command rule (per PV1) | `pid`, `pv1` | `obr`, `obx`, `orc` |
| ORM/OML command rule (per IN1) | `pid`, `in1` | `pv1`, `obr`, `obx`, `orc` |
| ORM/OML command rule (per OBR) | `pid`, `req`, `orc`, `obr` | `pv1`, `obx` |
| ORM/OML command rule (per OBX) | `pid`, `req`, `obx` | `pv1`, `orc`, `obr` |
| ORU command rule (per PV1) | `pid`, `pv1` | `obr`, `obx`, `orc` |

This per-segment dispatch model means an HL7 command rule **runs multiple
times per inbound message** — once per child segment as the message-type
handler iterates them. Rules must be safe to re-run across nulls and
across iterations of related segments.

**Rule-engine equation type used for HL7 command rules:** these are
*not* `EQTYPE_REGISTRATION` despite firing at registration-ish moments.
They run through a dedicated rule-execution path
(`hl7n_rules_process` → command-rule engine) that's separate from the
generic equation engine. Field-level effects (set demographics, override
lab fields, accept/reject events) are driven by the per-field
configuration in the HL7 Command Rules row, not by `EQTYPE_*` flags.

for setup, per-event tuning (`add_ur`, `lab_manditory`, field-level
update/clear/mandatory), and recommended patterns.

### Analyser result integration

When an analyser driver produces a result, the wire→storage flow is:

```
read_result()  →  populate LEVEL1_RESULT  →  test/UR lookup
              →  an_level1_save()  →  L1 store on disk
              →  EQTYPE_ANALYSER equations fire on the L1 record
              →  EQTYPE_L1VALIDATE equations during batch L1 validation
              →  EQTYPE_AUTOVAL during auto-validation step
              →  EQTYPE_TESTVALIDATE / EQTYPE_TESTACCEPT on validate
```

`EQTYPE_ANALYSER` equations run on the **incoming LEVEL1_RESULT** before
the result is promoted to a lab. They have access to the analyser-side
result subroutines (`an_result`, `an_results_flagged`, `an_error_check`,
etc., per the [Analyser](#analyser) section) but **not** to interactive
subroutines like `jump_to_test` — there's no UI context. Most of the
result-modification subroutines and `test_*` functions are valid, but
those marked "Not Analyser" in this doc are disallowed.

for the LEVEL1_RESULT structure that drives `an_*` subroutine inputs.

### Equation types by trigger source

A compact map of who fires which equation type:

| Source | Equation types triggered |
|---|---|
| Lab record save (`lab_data_close`) | `EQTYPE_TESTRECALC` |
| Test result entered manually | `EQTYPE_TESTRECALC`, then `EQTYPE_TESTVALIDATE` on validate, `EQTYPE_TESTACCEPT` on accept |
| Analyser inbound (any driver) | `EQTYPE_ANALYSER` |
| Level-1 batch validation pass | `EQTYPE_L1VALIDATE` |
| Auto-validation engine | `EQTYPE_AUTOVAL` |
| Patient/request registration | `EQTYPE_REGISTRATION` |
| Test added to request | `EQTYPE_REQUEST_ADD` |
| Test removed from request | `EQTYPE_REQUEST_RM` |
| Report engine (print) | `EQTYPE_REPORT` |
| Report on-screen display | `EQTYPE_REPDISP` |
| GSI / GAI generic invocation | `EQTYPE_GENERIC` |
| Billing module | `EQTYPE_BILLING`, `EQTYPE_MBSCHECK` |
| MFA flow | `EQTYPE_MFA` |
| Retrigger queue | `EQTYPE_RETRIGGER` |
| **HL7 inbound** (any direction) | **Not an `EQTYPE_*` invocation** — uses the dedicated `hl7n_rules_process` path driven by HL7 Command Rules (see above). Don't expect generic `EQTYPE_*` rules to fire from HL7 inbound. |

---

## Common Parameter Values

These parameter conventions are used throughout the report output subroutines.

### Position & Size

All x, y, width, height values used in report output functions are in **report units (hundredths of a millimetre, effectively tenths of a point in PostScript mode)**. The origin is the top-left of the page.

### Font / Colour Code (`font` parameter)

An integer combining font identifier and colour. Passed directly to the rendering engine. Use system font constants (configured in the application). A single integer may encode both a font and a colour.

### Point Size (`size` parameter)

Font size in **points** (e.g., `9` = 9pt, `12` = 12pt).

### Highlight Font / Highlight Size (`hfont`, `hsize` parameters)

Many output functions accept a normal and a highlight font/size pair. The highlight values are used automatically when the test result has an abnormal flag (HIGH or LOW). If the result is normal, `font`/`size` are used; if abnormal, `hfont`/`hsize` are used.

### Justification (`just` parameter)

A string code:

| Value | Meaning |
|---|---|
| `"L"` | Left-justified |
| `"C"` | Centre-justified |
| `"R"` | Right-justified |
| `"J"` | Full (paragraph) justification |
| `""` or `0` | Default (left) |

### Flags String (`flags` parameter)

Used by `output_results`, `output_cumresults`, `outcum_results`, `output_fullcum`, etc. A string containing one or more flag characters:

| Char | Meaning |
|---|---|
| `e` | Exact — output raw result without status formatting |
| `p` | Prefix — include result prefix |
| `T` | Top-align — align output to the top of the block |
| `R` | Right-align — right-align within column |
| `r` | Reversed — reverse display order |
| `s` | Small suffix |
| `m` | Medium format |
| `L` | Landscape orientation |
| `w` | Wide format |
| `H` | HL7 status mode |

### Mode (`mode` parameter)

Used by `output_testname`, `output_panelname`, `outcum_testname`, `outcum_panelname`:

| Value | Display |
|---|---|
| `0` | Full name |
| `1` | Mnemonic |
| `2` | Alias |
| `3` | Display name (falls back to full name) |

### Result Index (`result_idx` parameter)

Used by `result_format`, `cumresult_format`:

- `0` — current lab result
- `1..n` — nth loaded cumulative result (requires prior `loadcumulative` call)

### Test Status Flags (`TESTSTATUS_*`)

These are the bit flags on the `TEST.status` field. Most are read-only from equation context — only the `<`/`>` qualifier flags can be set/cleared via `test_set_status`/`test_clear_status`.

| Hex | Constant | Description |
|---|---|---|
| 0x0001 | `TESTSTATUS_MODIFIED` | Result was manually modified this session |
| 0x0002 | `TESTSTATUS_VALIDATED` | Test has been validated |
| 0x0004 | `TESTSTATUS_STATSDONE` | Result has been entered into statistics |
| 0x0008 | `TESTSTATUS_OVERDUE` | Test is overdue |
| 0x0010 | `TESTSTATUS_FLAGGED` | Test is flagged (generic flag) |
| 0x0020 | `TESTSTATUS_BILLINGDONE` | Billing has been processed |
| 0x0040 | `TESTSTATUS_RESULT` | A result value is present |
| 0x0080 | `TESTSTATUS_HOLD` | Test is on hold |
| 0x0100 | `TESTSTATUS_CALCULATED` | Result was calculated (not manually entered) |
| 0x0200 | `TESTSTATUS_DELTA` | Delta check threshold was breached (read by `delta()`) |
| 0x0400 | `TESTSTATUS_LAB_USE_ONLY` | Internal lab use — suppresses display to output |
| 0x0800 | `TESTSTATUS_CANCELLED` | Test/request has been cancelled |
| 0x1000 | `TESTSTATUS_LESS_THAN` | Result has `<` qualifier — **settable via `test_set_status(test, 1)`** |
| 0x2000 | `TESTSTATUS_GREATER_THAN` | Result has `>` qualifier — **settable via `test_set_status(test, 2)`** |
| 0x4000 | `TESTSTATUS_ACCEPTED` | Test result has been accepted |
| 0x8000 | `TESTSTATUS_SUSPEND` | Test is suspended |
| 0x10000 | `TESTSTATUS_HIGH` | Result is high (abnormal high) |
| 0x20000 | `TESTSTATUS_LOW` | Result is low (abnormal low) |
| 0x40000 | `TESTSTATUS_CRIT_HIGH` | Result is critically high |
| 0x80000 | `TESTSTATUS_CRIT_LOW` | Result is critically low |
| 0x100000 | `TESTSTATUS_CONTROL` | QC control result |
| 0x200000 | `TESTSTATUS_COMPLETED` | Computed by `test_calc_status()` |
| 0x400000 | `TESTSTATUS_SUPPRESSFMT` | Suppress formatting of result |
| 0x10000000 | `TESTSTATUS_DELETED` | Test has been deleted |
| 0x20000000 | `TESTSTATUS_MANDATORY` | Test is mandatory |
| 0x80000000 | `TESTSTATUS_TRFLAG` | Transfer flag |

The abnormal flags (`HIGH`, `LOW`, `CRIT_HIGH`, `CRIT_LOW`, `FLAGGED`) are collectively accessible as `TESTSTATUS_FLAGS`. Use `set_abnormal(test)` / `clear_abnormal(test)` to change abnormal state rather than manipulating these bits directly.

---

## Built-in Identifiers

### Read-only system variables (UPPERCASE)

| Name | Description |
|---|---|
| `AGE_DAYS` | Patient age in days |
| `SEX` | Patient sex |
| `LOCATION` | Current location |
| `WARD` | Ward |
| `DOCTOR` | Doctor name |
| `CONSULTANT` | Consultant name |
| `CLINNOTE` | Clinical note |
| `CATEGORY` | Request category |
| `ALERT` | Alert flag |
| `DIAGNOSIS` | Diagnosis |
| `FILEPREFIX` | File prefix |
| `SAMPLE_VOLUME` | Sample volume |
| `SAMPLE_PERIOD` | Sample period |
| `GSI_MODE` | GSI mode (Generic equations only) |
| `GSI_REGSTATUS` | GSI registration status (Generic only) |
| `QPS_REGSTATUS` | QPS registration status (Generic only) |

### Mutable system variables (UR_ / LAB_)

| Prefix | Fields |
|---|---|
| `UR_` | `DOB`, `NAME`, `GNAME`, `ADDRESS1`, `ADDRESS2`, `SUBURB`, `POSTCODE`, `SEX`, `ETHNICITY`, `FINCAT`, `SSCLIENT`, `SSSAMPLES`, `SSCRISP`, `SSPOLDAT`, `SSCRMCLS`, `GENFLAG1–8`, `GENNUMBER1–8`, `GENSTRING1–8` |
| `LAB_` | `GENFLAG1–8`, `GENNUMBER1–8`, `GENSTRING1–8` |

### Test and panel names

Test names are uppercase identifiers registered in the system (e.g. `SODIUM`, `POTASSIUM`).  
Panel names are uppercase identifiers for grouped test sets.

Indexed access:
```
SODIUM           /* current result value */
SODIUM[0]        /* first result */
SODIUM[0][0]     /* first result, first attribute */
PANEL[0]         /* first test in panel */
```

---

## Subroutines

**Column key:**
- **Params** — number of arguments
- **Eq Types** — valid equation types (see [Equation Types](#equation-types))

> **Important note on report output functions:** For ALL `output_*` and `outcum_*` functions, the **test or panel name is always the LAST argument**. The first two arguments are always `x, y` (position). This is the opposite of how many functions are documented in older references.

---

### Mathematical

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `abs(x)` | 1 | All | Absolute value of `x` |
| `exp(x)` | 1 | All | e raised to the power `x` |
| `ln(x)` | 1 | All | Natural logarithm of `x` |
| `log(x)` | 1 | All | Base-10 logarithm of `x` |
| `sqrt(x)` | 1 | All | Square root of `x` |
| `int(x)` | 1 | All | Truncate `x` to integer (towards zero) |
| `max(a, b)` | 2 | All | Maximum of two values |
| `min(a, b)` | 2 | All | Minimum of two values |
| `pow(base, exp)` | 2 | All | Raise `base` to the power `exp` |
| `rnd(x, decimals)` | 2 | All | Round `x` to `decimals` decimal places |
| `whole(x, unit)` | 2 | All | Round `x` down to nearest multiple of `unit` |

---

### String Operations

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `strlen(s)` | 1 | All | Returns length of string `s` |
| `strmkupper(s)` | 1 | All | Returns `s` converted to uppercase |
| `strmklower(s)` | 1 | All | Returns `s` converted to lowercase |
| `strcmp(s1, s2)` | 2 | All | Compare two strings; returns 0 if equal, <0 or >0 otherwise |
| `strcasecmp(s1, s2)` | 2 | All | Case-insensitive string compare; returns 0 if equal |
| `strsearch(s, pattern)` | 2 | All | Search for `pattern` in `s`; returns character position (1-based) or 0 |
| `strfind(s, list)` | 2 | All | Find first match of `s` in comma-separated `list`; returns 1 if found |
| `strstr(haystack, needle)` | 2 | All | Locate `needle` in `haystack`; returns position (1-based) or 0 |
| `strlines(s)` | 1 | Not Analyser | Returns count of lines in string `s` |
| `doclines(s, width)` | 2 | Not Analyser | Returns count of lines in `s` when word-wrapped to `width` characters |
| `substring(s, start, len)` | 3 | All | Extract `len` characters from `s` starting at position `start` (1-based) |
| `getlines(s, start, count)` | 3 | All | Extract `count` lines from `s` starting at line number `start` |
| `stripchars(s, chars)` | 2 | Generic | Remove all occurrences of characters in `chars` from `s` |
| `break_lines(s, width)` | 2 | All | Wrap `s` to `width` characters per line; returns reformatted string |
| `build_string(s, x, y, font)` | 4 | All | Build a formatted string with embedded position/font metadata for output |
| `h1flagmatch(flags, mask)` | 2 | All | Test HL1 flag field against `mask`; returns 1 if match |
| `ccom_search(code, field)` | 2 | All | Search coded comment `code` for `field`; returns matched text |
| `splitstr_count(s, delim)` | 2 | All | Count tokens in `s` split by delimiter string `delim` |
| `splitstr_get(s, delim, n)` | 3 | All | Get the `n`th token (1-based) from `s` split by `delim` |

---

### Type Conversion

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `atoi(s)` | 1 | All | Parse string `s` to integer |
| `atof(s)` | 1 | All | Parse string `s` to float |
| `itoa(n)` | 1 | All | Format integer `n` as string |
| `ftoa(x)` | 1 | All | Format float `x` as string |
| `testres_expand(result)` | 1 | All | Expand numeric test result code to its display string |

---

### List Operations

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `listgenerate(start, count)` | 2 | All | Generate a comma-separated list of integers from `start` for `count` items |
| `listcount(list)` | 1 | All | Count elements in comma-separated `list` |
| `listelement(list, n)` | 2 | All | Get `n`th element from comma-separated `list` (1-based) |
| `listinsert(list)` | 1 | All | Insert item into list |
| `listremove(list)` | 1 | All | Remove item from list |
| `listinsert_test(list, test)` | 2 | All | Insert test name into list |
| `listremove_test(list, test)` | 2 | All | Remove test name from list |

---

### Date and Time

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `days_difference(date1, date2)` | 2 | All | Number of days between two dates |
| `days_add(date, n)` | 2 | All | Add `n` days to `date`; returns new date |
| `mins_difference(dt1, dt2)` | 2 | All | Number of minutes between two date-times (`dd/mm/yy hh:mm` format) |
| `date_to_words(date)` | 1 | All | Format `date` as a human-readable string (e.g. "12 January 2024") |

---

### Data Loading

These subroutines populate `labarray` (the internal cumulative record buffer) for subsequent access via test subscripts (`TEST[n]`) and cumulative output functions. All load functions replace any previously loaded data. Returns the number of records actually loaded.

**`loadcumulative` vs `loadhistorical`:** Both retrieve previous lab records but differ in data source, validation filtering, position anchoring, and sort behaviour:

**1. Data source:**
- `loadhistorical` — reads from the **UR lab list** (`urfile_get_labnos`), which contains every lab number registered to the patient's UR record. This is the complete patient lab history.
- `loadcumulative` — reads from the **cumulative report file** (`repcums`), which only contains labs that have previously had a cumulative report generated for them. Labs that have never been printed to a cumulative report **will not appear**.

**2. Positional anchor (where is "now"):**
- `loadhistorical` — the UR lab list is sorted by registration time (oldest first) then **reversed** in memory (newest first). The function then **finds the current `labno` in the list** and starts loading from the next position (i.e., records older than the current one). If the current lab is new and has not yet been saved to the UR record, it will not be found in the list and the function returns 0 records.
- `loadcumulative` — the `labno` anchor parameter is accepted but **not used** (the code returns before reaching the branch that would use it). It loads all records from the repcums file for the patient without filtering by position relative to the current lab. It does not know or care where the current record sits in the patient history.

**3. Validation filter:**
- `loadhistorical` — **validated results only**. Requires both `LHF_DATA_VALID` and `LHF_TESTS_VALIDATED` flags on each record. Unvalidated, in-progress labs are skipped.
- `loadcumulative` — **all results**, validated or not. Only filters out deleted records.

**4. Sort and display state:**
- `loadcumulative` — after loading, **sorts** records by collection date, then creation date, then lab number (`labsort`). Also sets internal pagination state (`A4cumsize`, `A4_pageno`) for A4 cumulative report layout. Use this when rendering cumulative tables on reports.
- `loadhistorical` — no sort, no pagination state. Use in equation logic for delta checks or trend reads.

- `loadcumulativereverse` — same as `loadcumulative` (repcums source, all results, sorted) but in reverse chronological order (most recent first).
- `loadlinkedresults` — loads validated results from the UR lab list like `loadhistorical`. **"Linked" here refers to UR-linked patient records** — when a patient has been registered under multiple UR numbers that have been linked together in the system (UR Linking), labs from all linked URs are included. In practice the implementation is identical to `loadhistorical`: both call `labhist_get_prev` with the same mode flags, and both include labs from linked URs. If you call one after the other for the same patient in the same equation, the second call uses the cached result from the first.

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `loadcumulative(count, testlist)` | 2 | All | Load up to `count` previous lab records (**all results including unvalidated**), sorted chronologically; required before `output_cumresults`, `outcum_*`, `output_fullcum`, `result_count` |
| `loadcumulativereverse(count, testlist)` | 2 | All | Same as `loadcumulative` (all results) but sorted most-recent-first; use when cumulative columns read newest-to-oldest |
| `loadhistorical(count, testlist)` | 2 | All | Load up to `count` previous lab records (**validated results only**); raw database order, no sort; use in equation logic for delta checks or trend reads via `TEST[n]` subscripts |
| `loadlinkedresults(count, testlist)` | 2 | All | Load validated results from the UR lab list including **UR-linked patient records** (other UR numbers linked to the same patient); same mechanism as `loadhistorical` — raw order, position-anchored to current labno |
| `loadlabrecs(type, count, testlist)` | 3 | All | Load up to `count` lab records of `type` for tests in `testlist`; `type` selects the record category |
| `load_patsum_ur(type)` | 1 | All | Load patient summary data from UR record; `type` selects summary category |
| `load_patsum_lab(type)` | 1 | All | Load patient summary data from lab records |
| `loadcrisps(count, type)` | 2 | All | Load up to `count` CRISP case records of `type` associated with the current lab. **CRISP = a Scientific Services / Forensic Biology case record** — a case file that groups forensic specimens under a single case number (with police reference, priority, category, status). Only relevant in the Forensic Biology / Scientific Services module. Requires the `FTAE` specimen type to be configured. |
| `load_order_labs(type)` | 1 | All | Load lab records associated with an eOrder |
| `load_dnabatch(type)` | 1 | All | Load a DNA batch record of `type` |
| `load_user_details(user)` | 1 | All | Load details for the user/doctor identified by `user` code; result accessible via user detail built-ins |
| `load_testnote(test)` | 1 | All | Load notes attached to `test` |

---

### Test and Request Management

**`test_present` vs `test_ordered`:** These look similar but check different data structures:
- `test_present(test)` — scans the lab's **test result array** (`lab->test[]`). Returns 1 if the test type has an entry in the array — i.e. a result slot exists, whether or not there is an actual result value. A test auto-added by the system (e.g. a reflex) is present but may not be ordered.
- `test_ordered(test)` — scans the lab's **requests list** (`lab->requests[]`). Returns 1 if the test or panel was explicitly requested. A test can be present (has a result slot) without having been ordered, and conversely a test can be ordered but cancelled before being added to the lab.
- `test_modified(test)` — compares the current test result against the **original data snapshot** taken when the lab record was opened. Returns 1 if the result value, result type, or result status differs from the snapshot. Detects manual overrides and corrections during the current editing session.

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `test_present(test)` | 1 | All | Returns 1 if `test` has a result slot in the lab — checks `lab->test[]` array; true for auto-added tests even if not explicitly ordered |
| `test_ordered(test)` | 1 | All | Returns 1 if `test` was explicitly requested — checks `lab->requests[]`; does not return true for auto-added tests not in the original request |
| `test_modified(test)` | 1 | All | Returns 1 if `test` result differs from the original snapshot taken when the lab was opened; detects manual corrections during current session |
| `test_validated(test)` | 1 | Not Analyser | Returns 1 if `test` has been validated |
| `test_result(test)` | 1 | Not Analyser | Returns the current result value for `test` |
| `test_val(test)` | 1 | Not Analyser | Get test value (SIR 78715) |
| `test_res(test)` | 1 | Not Analyser | Get test result code (SIR 78715) |
| `test_loadvalue(test)` | 1 | All | Load the stored result value for `test` |
| `test_setvalue(test, value)` | 2 | All | Set the result `value` for `test` |
| `test_set_status(test, status)` | 2 | Not Analyser | Set a result qualifier on `test`; `status` must be `1` (`<` less-than qualifier) or `2` (`>` greater-than qualifier) — any other value is silently ignored |
| `test_check_status(test, status)` | 2 | Not Analyser | Returns 1 if the qualifier is set; `status` 1=check `<`, 2=check `>`, 3=check either; any other value returns 0 |
| `test_clear_status(test, status)` | 2 | Not Analyser | Clear a result qualifier on `test`; `status` must be `1` (`<`) or `2` (`>`) — any other value is silently ignored |
| `test_resultstring(test, type, idx)` | 3 | Generic | Get result string field `type` at index `idx` for `test` |
| `test_on_lab(test)` | 1 | Generic | Returns 1 if `test` is on the current lab record |
| `test_on_containers(test)` | 1 | All | Returns 1 if `test` is assigned to a container (DEV-2229) |
| `retest_interval(test)` | 1 | All | Get the retest interval for `test` |
| `accept(test)` | 1 | Not Analyser | Accept `test` result |
| `validate(test)` | 1 | Not Analyser | Validate `test` result |
| `validate_user(test, user)` | 2 | All | Validate `test` result attributed to `user` code |
| `add_request(test)` | 1 | All | Add `test` to the current request |
| `remove_request(test)` | 1 | All | Remove `test` from the current request |
| `jump_to_test(test)` | 1 | Interactive | Jump input focus to `test` field; only valid in interactive (non-report, non-analyser) contexts |
| `testlist_item(list, n)` | 2 | All | Get `n`th test from test list (1-based) |
| `testing_method(test)` | 1 | All | Get the testing method code for `test` |
| `testing_lab(test)` | 1 | All | Get the testing lab code for `test` |
| `testing_lab_check(test, lab)` | 2 | All | Returns 1 if `test` is performed at lab `lab` |
| `set_abnormal(test)` | 1 | All | Flag `test` result as abnormal (SIR 76731) |
| `clear_abnormal(test)` | 1 | All | Clear the abnormal flag on `test` (SIR 76731) |
| `suppress_result(test)` | 1 | Not Analyser | Suppress `test` result from output |
| `unsuppress_result(test)` | 1 | Not Analyser | Unsuppress a previously suppressed `test` result |
| `suppressed(test)` | 1 | Not Analyser | Returns 1 if `test` result is suppressed |

---

### Reference Range and Delta

**`delta(test)` is a boolean flag check, not the actual delta amount.** It returns 1 if the `TESTSTATUS_DELTA` bit is set on the test — meaning the system has already determined the test has breached its configured delta check threshold. It does not return the numeric difference between the current and previous result. Use `check_delta()` to test a specific numeric range, or read a prior result via `TEST[1]` after `loadhistorical` to compute the difference yourself.

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `refrange(test)` | 1 | Not Analyser | Returns the reference range string for `test` as configured for the current patient demographics |
| `delta(test)` | 1 | Not Analyser | Returns 1 (boolean) if the `TESTSTATUS_DELTA` flag is set — i.e. the system flagged this test as having breached its delta threshold. Does NOT return the numeric delta amount. |
| `check_refrange(test, low, high, unit)` | 4 | All | Returns 0 if within range, ±1 for low/high, ±2 for critical; `test` is the first arg |
| `check_delta(test, low, high, unit)` | 4 | All | Returns 1 if `test` delta is within the given range bounds |

---

### Result Formatting

**`result_format` vs `cumresult_format`:** Both call the same underlying implementation. The only difference is that `cumresult_format` sets an internal `cumulative_A4` flag before executing. This flag changes how `test_locate` resolves subscript indices: with `cumulative_A4` active, `labarray[0]` is the first cumulative record (same as report rendering mode), whereas without it `labarray[0]` is the current lab. Use `cumresult_format` when rendering results in an A4 cumulative report context; use `result_format` in standard equations and non-cumulative reports.

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `result_format(test, result_idx, decimals)` | 3 | All | Format `test` result at `result_idx` (0=current, 1..n=nth record in labarray after a load call) to string with `decimals` decimal places; returns empty string if the result is incomplete or status-suppressed |
| `result_format_images(test, result_idx, decimals)` | 3 | All | Same as `result_format` but includes embedded image references in the output (PostScript report mode) |
| `cumresult_format(test, result_idx, decimals)` | 3 | All | Same as `result_format` but with `cumulative_A4` indexing mode active — use inside A4 cumulative report equations |
| `result_count(test)` | 1 | All | Returns the number of tests matched in a testlist string (takes a string arg, not a test identifier); does not count loaded cumulative records |
| `result_match(test, value, op, n)` | 4 | All | Returns 1 if `n`th result for `test` matches `value` using comparison `op` |
| `result_allow(test, list)` | 2 | All | Allow only result codes in `list` for `test` |
| `result_disallow(test, list)` | 2 | All | Disallow result codes in `list` for `test` |
| `testres_encode(test, result_str, start, len)` | 4 | All | Encode a substring of `result_str` (starting at char `start` for `len` chars) as an internal result code for `test` |

---

### Bit Operations

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `checkbits(value, mask, bits)` | 3 | All | Returns 1 if `(value & mask) == bits`; used to test flag fields |
| `setbits(value, bits)` | 2 | All | Set `bits` in `value`; returns new value |
| `clearbits(value, bits)` | 2 | All | Clear `bits` in `value`; returns new value |

---

### Coded Comments and SNOMED

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `codedcomment(code)` | 1 | All | Get the text for coded comment `code` |
| `ccom_expand(code)` | 1 | All | Expand coded comment `code` to full text |
| `snomed_expand(code)` | 1 | All | Expand SNOMED code to its display text |

---

### Report Output Standard

These subroutines are only valid in **Report** equations unless otherwise noted.

> **Parameter order rule:** For all `output_*` positioning functions, the argument order is always: `x, y, [font parameters], [content parameters], test_or_text_LAST`.

#### Text & Accreditation

| Subroutine | Params | Description |
|---|---|---|
| `output_text(x, y, font, size, just, text)` | 6 | Output `text` (string, variable, or test name) at position `(x, y)` with `font`/`size`/`just` |
| `output_sigtext(x, y, font, size, just, text)` | 6 | Output significant text at position; triggers change-detection for AUSCARE |
| `output_accreditation(x, y, font, size, just, test_or_list)` | 6 | Output accreditation statement for `test_or_list` at `(x, y)` |
| `output_accred_symbol(x, y, font, size, options, test_list)` | 6 | Output accreditation symbol for tests in `test_list`; returns x-offset for chaining with `output_testname` |
| `output_guideline(x, y, font, size, mode, just, test_or_list)` | 7 | Output guideline text; `mode` selects which guideline (0=default); requires FSS licence |
| `output_testnote(x, y, font, size, just, options, test)` | 7 | Output note text for `test` at `(x, y)` |
| `output_codes(code_group, code_info, coding_sys, scope, test, attr_sep, options)` | 7 | Output coding system codes (SNOMED, LOINC, etc.); `scope`: 0=all, 1=lab, 2=specimen; `attr_sep` sets separator between code attributes |

#### Lines, Boxes & Shapes

| Subroutine | Params | Description |
|---|---|---|
| `output_box(x, y, w, h, linewidth, fill, colour)` | 7 | Draw a rectangle at `(x,y)` size `(w,h)`; `fill` and `colour` are floating-point (0.0–1.0 greyscale) |
| `output_line(x1, y1, x2, y2, linewidth, colour, end_style)` | 7 | Draw a line from `(x1,y1)` to `(x2,y2)`; `end_style` 0=plain, 1=arrow |
| `output_test_box(x, y, h, w, colour, test_list, count)` | 7 | Draw coloured boxes for tests in `test_list`; `h`/`w` box dimensions; `count` is items per row |

#### Test / Panel Name, Result, Units

| Subroutine | Params | Description |
|---|---|---|
| `output_testname(x, y, nfont, nsize, hfont, hsize, just, mode, accred_pfx, test)` | 10 | Output test name for `test` at `(x,y)`; `nfont`/`nsize` normal, `hfont`/`hsize` abnormal highlight; `mode` 0=name,1=mnem,2=alias,3=display; `accred_pfx` 0=auto-prefix accred symbol |
| `output_panelname(x, y, nfont, nsize, hfont, hsize, just, mode, accred_pfx, panel)` | 10 | Output panel name for `panel`; same parameters as `output_testname` |
| `output_results(x, y, nfont, nsize, hfont, decimals, just, flags, width, test)` | 10 | Output result block for `test`; `decimals` decimal places; `flags` string (see [Flags String](#flags-string)); `width` column width; abnormal results use `hfont` |
| `output_units(x, y, nfont, nsize, hfont, hsize, just, test)` | 8 | Output units for `test`; abnormal results use `hfont`/`hsize` |
| `output_refrange(x, y, nfont, nsize, hfont, hsize, just, test)` | 8 | Output reference range string for `test` |
| `output_testmethod(x, y, nfont, nsize, hfont, hsize, just, test)` | 8 | Output testing method for `test` |
| `output_lowprec(x, y, nfont, nsize, hfont, hsize, just, test)` | 8 | Output low precision limit for `test`; requires FSS licence |
| `output_highprec(x, y, nfont, nsize, hfont, hsize, just, test)` | 8 | Output high precision limit for `test`; requires FSS licence |
| `output_uncertainty(x, y, nfont, nsize, hfont, hsize, just, test)` | 8 | Output measurement uncertainty for `test`; requires uncertainty licence |
| `output_sensitivities(x, y, nfont, nsize, hfont, hsize, just, w, h, test)` | 10 | Output antibiotic sensitivity panel for `test`; `w`/`h` cell dimensions |
| `output_testspecimen(test, type, options)` | 3 | Output specimen type for `test`; `type` and `options` control format |

#### Documents, Images & Special

| Subroutine | Params | Description |
|---|---|---|
| `output_document(x, y, font, size, just, text_content, lpp, page, colour)` | 9 | Output multi-page document content; `lpp`=lines per page, `page`=current page number, `colour`=background colour |
| `output_worddoc(x, y, font, size, just, text_content, lpp, page, colour)` | 9 | Output Word document content (DEV-747); same parameters as `output_document` |
| `output_eps(x, y, scale_x, scale_y, rotation_deg, filename)` | 6 | Embed PostScript EPS file; `scale_x`/`scale_y` are floats (1.0=100%), `rotation_deg` in degrees |
| `output_rawimage(x, y, h, w, filename, spare)` | 6 | Output raw image from `filename` at `(x,y)` with height `h` and width `w` |
| `output_annoteimage(x, y, w, h, img_id)` | 5 | Output annotated image with internal ID `img_id` (LAST param) at `(x,y)` size `(w,h)` |
| `annoteimage_output(img_id, x, y, w, h, style, options)` | 7 | Output annotated image `img_id` (FIRST param) with annotation overlays; `style` and `options` control rendering |
| `output_signature(x, y, h, w, user)` | 5 | Output signature graphic for `user` (LAST param) at `(x,y)` height `h` width `w` |
| `output_barcode(x, y, width, bar_width, height, rotation, type, data)` | 8 | Output barcode; `data` is the barcode content (LAST, string); `rotation` in degrees; `type` is barcode format code |

#### HL7 / SMS / Email / URL output (valid in All equation types)

| Subroutine | Params | Description |
|---|---|---|
| `output_hlseven(data)` | 1 | Send HL7 message `data` |
| `output_hl7(data)` | 1 | Send HL7 message (alias) |
| `send_hl7(data)` | 1 | Send HL7 message (alias) |
| `send_sms(number, message, options)` | 3 | Send SMS to `number` with `message` |
| `output_email(x, y, font, size, colour, email_addr, text)` | 7 | Output/send email; in Report context renders a clickable link at `(x,y)` |
| `output_url(x, y, font, size, colour, url, text)` | 7 | Output URL hyperlink at `(x,y)`; `text` is the displayed link text |

#### Control

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `output_control(flag)` | 1 | Report | Output control character/flag |
| `output_raw(data)` | 1 | Report | Output raw PostScript data bytes |
| `output_inv_body(lines_per_page, y, current_line, fontsize)` | 4 | Report | Output invoice body lines; returns remaining line count; use in `while` loop |
| `output_ncidd_xml()` | 0 | Report | Output NCIDD XML data |
| `suppress_output(flag)` | 1 | Report | Suppress report output sections (SIR 76942) |
| `report_validated(flag)` | 1 | Report | Check or set report validated state |
| `suppress_address()` | 0 | Report | Suppress address block on report (DEV-1807) |
| `final_validation(flag)` | 1 | Report | Perform final validation step (SIR 81310) |
| `output_tm_alert(type)` | 1 | All | Output transfusion medicine alert of `type` |

---

### Report Output Cumulative

These subroutines are only valid in **Report** equations and work on data loaded via `loadcumulative` / `loadcumulativereverse`.

> **Parameter order rule:** All `outcum_*` display functions take `page` as the FIRST argument and the test/panel list as the LAST argument. `cols` and `rows` control how many lab columns and test rows appear on each page.

#### Batch / Full Cumulative

| Subroutine | Params | Description |
|---|---|---|
| `output_fullcum(x, y, cols, col_offsets, nfont, nsize, hfont, hsize, just, box, flags, mode, testlist)` | 13 | Output a complete cumulative block; `col_offsets` is a pipe-separated offset string; `box` is a `"h\|w\|n\|color"` string; `flags` see flag chars |
| `output_cumresults(x, y, nfont, nsize, hfont, decimals, just, flags, width, test)` | 10 | Output cumulative results grid for `test`; same parameters as `output_results` |
| `output_cumrefrange(x, y, nfont, nsize, hfont, hsize, just, test)` | 8 | Output cumulative reference ranges for `test` |
| `output_cumgraph(x, y, w, h, test, style, flags, options)` | 8 | Output cumulative trend graph; `test` is the 5th argument; `w`/`h` graph dimensions |
| `outcum_pagecount(test, cols, rows)` | 3 | Returns number of pages needed for cumulative display of `test` with `cols` lab columns and `rows` test rows per page |
| `isnoncum()` | 0 | Returns 1 if report is non-cumulative (DEV-1807) |
| `cumpageno(n)` | 1 | Get cumulative page number `n` |

#### Per-column Output (11 params)

These functions output a single data element for each test row, paged across lab columns.

| Subroutine | Params | Description |
|---|---|---|
| `outcum_testname(page, x, y, cols, rows, nfont, nsize, hfont, hsize, just, mode, test)` | 12 | Output test name in cumulative column; `mode` 0=name, 1=mnem, 2=alias, 3=display |
| `outcum_panelname(page, x, y, cols, rows, nfont, nsize, hfont, hsize, just, mode, panel)` | 12 | Output panel name in cumulative column |
| `outcum_results(page, x, y, cols, rows, nfont, nsize, hfont, decimals, just, flags, test)` | 12 | Output result values in cumulative grid |
| `outcum_units(page, x, y, cols, rows, nfont, nsize, hfont, hsize, just, test)` | 11 | Output units in cumulative column |
| `outcum_refrange(page, x, y, cols, rows, nfont, nsize, hfont, hsize, just, test)` | 11 | Output reference range in cumulative column |
| `outcum_testmethod(page, x, y, cols, rows, nfont, nsize, hfont, hsize, just, test)` | 11 | Output test method in cumulative column |
| `outcum_lowprec(page, x, y, cols, rows, nfont, nsize, hfont, hsize, just, test)` | 11 | Output low precision in cumulative column |
| `outcum_highprec(page, x, y, cols, rows, nfont, nsize, hfont, hsize, just, test)` | 11 | Output high precision in cumulative column |
| `outcum_accreditation(page, x, y, cols, rows, font, size, just, test_list)` | 9 | Output accreditation in cumulative column |
| `outcum_guideline(page, x, y, cols, rows, font, size, mode, just, test_list)` | 10 | Output guideline text in cumulative column; `mode` selects guideline type |

#### Audit Output (7 params each)

`page` selects the audit page; `rows` is items per column; `direction` 0=vertical, 1=horizontal.

| Subroutine | Params | Description |
|---|---|---|
| `outcum_auditload(test, cols, rows)` | 3 | Load audit data for cumulative display |
| `outcum_auditpages(test)` | 1 | Get number of audit pages |
| `outcum_audituser(page, x, y, rows, font, size, direction)` | 7 | Output audit user in cumulative column |
| `outcum_auditdate(page, x, y, rows, font, size, direction)` | 7 | Output audit date in cumulative column |
| `outcum_audittest(page, x, y, rows, font, size, direction)` | 7 | Output audit test name in cumulative column |
| `outcum_auditlabg(page, x, y, rows, font, size, direction)` | 7 | Output audit lab group in cumulative column |
| `outcum_audittext(page, x, y, rows, font, size, direction)` | 7 | Output audit text in cumulative column |
| `outcum_auditlab(page, x, y, rows, font, size, direction)` | 7 | Output audit lab in cumulative column |

---

### LIS Output

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `lis_testlist(type, options)` | 2 | Report | Get list of tests for LIS output; `type` selects test category |
| `lis_testcount(type)` | 1 | Report | Count tests for LIS output of `type` |

---

### Billing

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `output_billing(lines_per_page, y, current_line, pointsize, str1, str2)` | 6 | Report | Output billing line items (internal use); use within a `while` loop; returns remaining lines |
| `output_publicbill(x, y, w, h, font, size, style, flags, width, options)` | 10 | Report | Output public billing block |
| `bill_line_set(x, font, size, just, identifier)` | 5 | Report | Define a billing output column: position `x`, font/colour `font`, point size `size`, justification `just`, billing field `identifier` code |
| `bill_mline_set(x, line, font, size, just, identifier)` | 6 | Report | Define a multi-line billing output column; `line` selects the line within the item |
| `bill_gen_inv_text(options)` | 1 | All | Generate invoice text with `options` |
| `get_billing_item(index, field)` | 2 | Not AN + Billing | Get `field` value from billing item at `index` (SIR 78003) |
| `set_billing_item(index, field, value)` | 3 | Not AN + Billing | Set `field` on billing item at `index` to `value` (SIR 78003) |
| `get_billing_item_count()` | 0 | Not AN + Billing | Returns count of billing items (SIR 78003) |
| `ordering_lab()` | 0 | Not AN + Billing | Get the ordering lab code (SIR 78003) |
| `billing_lab(index)` | 1 | Not AN + Billing | Get billing lab code for item at `index` (SIR 78003) |

---

### Analyser

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `analyser(test)` | 1 | Not Analyser | Get the analyser code for `test` |
| `an_program(test)` | 1 | All | Get the analyser program for `test` |
| `an_result(test)` | 1 | Analyser + L1V | Get the raw analyser result for `test` |
| `an_results_match(test)` | 1 | All | Returns 1 if analyser results match expected for `test` |
| `an_results_flagged(test)` | 1 | All | Returns 1 if analyser results have flags for `test` |
| `an_error_check(test)` | 1 | All | Returns 1 if analyser reported an error for `test` |
| `an_event_log(test)` | 1 | All | Get analyser event log for `test` |
| `an_result_status(test, status)` | 2 | Analyser | Get analyser result status code for `test` (DEV-1936) |
| `an_change_test(from, to)` | 2 | Analyser | Change analyser test mapping from `from` to `to` (DEV-1936) |
| `an_add_test_result(test)` | 1 | Analyser | Add a test result from analyser data for `test` |

---

### External Enquiry ee_

External Enquiry (`ee_`) functions operate on datasets for **External Quality Assurance (EQA) enquiry programs** — the `ee` prefix stands for "external enquiry" (`labeenq` in the source). They load, compute, and display statistical results from EQA/external proficiency testing data. Requires the `CFG_ENABLE_COMPLEX_STATISTICS` licence.

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `ee_set(dataset, name, value)` | 3 | All | Set `value` for parameter `name` in `dataset` (integer 1–9) |
| `ee_clear(dataset)` | 1 | All | Clear all values in `dataset` |
| `ee_calculate(result_set, input_set2, input_set1)` | 3 | All | Run statistical calculation; returns a result object used by other ee_* functions |
| `ee_value(result, type, n)` | 3 | All | Get `n`th value of `type` from `result` dataset |
| `ee_max_value(result, type, n)` | 3 | All | Get maximum value from `result` dataset |
| `ee_min_value(result, type, n)` | 3 | All | Get minimum value from `result` dataset |
| `ee_mean_value(result, type, n)` | 3 | All | Get mean from `result` dataset |
| `ee_median_value(result, type, n)` | 3 | All | Get median from `result` dataset |
| `ee_freq(result, type, n)` | 3 | All | Get frequency count in `result` dataset |
| `ee_max_freq(result, type, n)` | 3 | All | Get maximum frequency in `result` dataset |
| `ee_min_freq(result, type, n)` | 3 | All | Get minimum frequency in `result` dataset |
| `ee_mean_freq(result, type, n)` | 3 | All | Get mean frequency in `result` dataset |
| `ee_median_freq(result, type, n)` | 3 | All | Get median frequency in `result` dataset |
| `ee_name(result, type, n)` | 3 | All | Get name label for `n`th entry in `result` dataset |
| `ee_plot(result, set, x, y, w, h, font, size, just, flags, options, min, max)` | 13 | All | Plot EE data as graph at `(x,y)` size `(w,h)`; `min`/`max` override axis range |

---

### Images and Annotations

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `images_load(rec_type, record_key, use_lab_record, image_type_name, include_all)` | 5 | All | Load images; `rec_type` 0=UR 1=Lab; `record_key` is lab index; `use_lab_record` 1=look up from loaded lab; `image_type_name` mnemonic string (empty=all); `include_all` 1=include non-printable |
| `images_load_links(rec_type, record_key, use_lab_record, image_type_name, include_all)` | 5 | All | Load image links via UR links (DEV-3673); same parameters as `images_load` |
| `image_count()` | 0 | All | Returns count of loaded images (SIR 77286) |
| `image_output(index, x, y, scale)` | 4 | All | Output loaded image at `index` to position `(x,y)`; `scale` is a float (1.0=original size) |
| `image_text(index, field)` | 2 | All | Get text metadata `field` from loaded image at `index` |
| `image_width(index)` | 1 | All | Get width of loaded image at `index` |
| `image_height(index)` | 1 | All | Get height of loaded image at `index` |
| `annotate_value(test)` | 1 | All | Get annotation value for `test` |
| `annotate_text(test)` | 1 | All | Get annotation text for `test` |
| `annotate_select(test)` | 1 | All | Get selected annotation for `test` |
| `annotate_load(test, options)` | 2 | All | Load annotations for `test` with `options` |

---

### DNA and Genetic

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `dna_test(test)` | 1 | All | Returns 1 if `test` is a DNA test |
| `load_dnabatch(batch)` | 1 | All | Load a DNA batch record |
| `add_dna_entry(is_result, value, field_name, options)` | 4 | All | Add a DNA entry field; `is_result` 0=sample, 1=result; `field_name` e.g. "LABNO", "TEST", "URNO" |
| `build_dna_entry()` | 0 | All | Commit/store the current DNA entry |
| `add_dna_string(is_result, value, field_name)` | 3 | All | Add a string field to a DNA entry |
| `decode_string(encoded, options)` | 2 | All | Decode an encoded DNA string |
| `add_dna_wflow(test, step, options)` | 3 | All | Add a DNA workflow step |
| `dna_wflow_result(test, step, field, n)` | 4 | All | Get result `field` from DNA workflow step `step` at index `n` |
| `dna_load_profile(profile)` | 1 | All | Load a DNA profile definition |
| `dna_num_prof_items(profile)` | 1 | All | Count items in DNA profile `profile` |
| `dna_prof_items(profile, n)` | 2 | All | Get `n`th item from DNA profile `profile` |
| `dna_return_sample(test)` | 1 | AutoVal + Generic | Mark a DNA sample for return |

---

### Microbiology and Organisms

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `micro_setresult(test, organism, result)` | 3 | All | Set microbiology `result` for `organism` on `test` |
| `micro_countresult(test, organism, field)` | 3 | All | Count microbiology results matching `organism` criteria for `test` |
| `micro_suppress(test, organism)` | 2 | All | Suppress microbiology result for `organism` on `test` |
| `micro_unsuppress(test, organism)` | 2 | All | Unsuppress microbiology result for `organism` on `test` |
| `organism_present(organism)` | 1 | Not Analyser | Returns 1 if `organism` is present in results |
| `organism_group_present(group)` | 1 | Not Analyser | Returns 1 if any organism in `group` is present |
| `organism_species_present(species)` | 1 | Not Analyser | Returns 1 if organism `species` is present |
| `organism_group_check(organism, group)` | 2 | Not Analyser | Returns 1 if `organism` belongs to `group` |
| `organism_species_check(organism, species)` | 2 | Not Analyser | Returns 1 if `organism` matches `species` |

---

### Specimen and Containers

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `specimen(test, field)` | 2 | All | Get specimen `field` for `test` |
| `add_to_container(test, container)` | 2 | All | Assign `test` to `container` (SIR 76728) |
| `get_container(test)` | 1 | All | Get the container code for `test` (SIR 82680) |
| `get_same_tube_tests(test)` | 1 | All | Get list of tests sharing the same container as `test` |
| `count_spare_containers(type)` | 1 | All | Count available spare containers of `type` (DEV-2229) |
| `add_to_spare_containers(test, type, options)` | 3 | All | Add `test` to spare container pool of `type` (DEV-2229) |
| `unique_tube_count()` | 0 | All | Returns count of unique tubes/containers |
| `get_unique_tube(index, field)` | 2 | All | Get `field` from `n`th unique tube (0-based `index`) |

---

### Batch and Plate

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `batch_create(type)` | 1 | All | Create a new batch record of `type` |
| `batch_store()` | 0 | All | Save/commit the current batch record |
| `plate_search(plate, options)` | 2 | All | Search for plate record `plate` |
| `find_batch_sample(batch, index)` | 2 | All | Find sample at `index` within `batch` |
| `find_batch_consum(batch, index)` | 2 | All | Find consumable at `index` within `batch` |
| `find_batch_equip(batch, index)` | 2 | All | Find equipment at `index` within `batch` |
| `find_batch_plate(batch)` | 1 | All | Find a plate record within `batch` |
| `find_batch_pos(batch, position)` | 2 | All | Find entry at `position` within `batch` |
| `find_batch_vuser(batch)` | 1 | All | Find a virtual user within `batch` |
| `get_previousbatch(type, count, options)` | 3 | All | Get up to `count` previous batch records of `type` |

---

### Accreditation

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `nata_accred(test)` | 1 | All | Returns 1 if `test` has NATA accreditation |
| `accred_symbol(test)` | 1 | All | Get accreditation symbol code for `test` |
| `get_accred_status(test)` | 1 | All | Get accreditation status for `test` |
| `check_accreditation(test, type)` | 2 | Report | Check accreditation `type` for `test` |
| `accred_report_statement(type)` | 1 | All | Get accreditation report statement text for `type` |

---

### Alerts and Notifications

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `add_alert(type)` | 1 | All | Add an alert of `type` to the current record |
| `remove_alert(type)` | 1 | All | Remove alert of `type` |
| `alert_added(type)` | 1 | All | Returns 1 if alert of `type` has been added |
| `add_uralert(type)` | 1 | All | Add a UR-level alert of `type` |
| `add_phone_notify(type)` | 1 | All | Add a phone notification entry of `type` |
| `set_phone_notify(type, number, message, options)` | 4 | All | Configure phone notification: `type` category, `number` phone number, `message` content, `options` |
| `get_phone_notify(index)` | 1 | All | Get phone notification entry at `index` |

---

### Terminology

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `term_get_disp(system, code, options)` | 3 | All | Get display name for terminology `code` in `system` (e.g. "SNOMED") |
| `term_get_pref(system, code, options)` | 3 | All | Get preferred name for `code` in `system` |
| `term_get_fsn(system, code, options)` | 3 | All | Get fully-specified name for `code` in `system` |
| `term_get_cmurl(system, code, options)` | 3 | All | Get CM URL for `code` in `system` |
| `term_get_cmver(system, code, options)` | 3 | All | Get CM version for `code` in `system` |
| `term_get_code(system, disp, options)` | 3 | All | Look up terminology `code` by display name `disp` in `system` |
| `is_indication_code_present(system, code, options)` | 3 | All | Returns 1 if indication `code` from `system` is present on request |

---

### Order and eOrder

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `get_order_value(test, field)` | 2 | All | Get `field` from the order record for `test` |
| `get_form_value(form, field)` | 2 | All | Get `field` from request form `form` |
| `get_paragraph(text, index, options)` | 3 | All | Get paragraph at `index` from text block `text` |
| `format_visit_hist(type, count, options)` | 3 | All | Format `count` visit history records of `type` |
| `apply_stringency(test, level)` | 2 | All | Apply stringency `level` to `test` |
| `get_xml_info(tag, options)` | 2 | Generic | Get value of XML `tag` from embedded XML on request |
| `get_order_id(test)` | 1 | All | Get the order ID for `test` |
| `get_order_id_list()` | 0 | All | Get list of all order IDs on current lab |
| `assign_order_id(test_from, test_to)` | 2 | All | Copy the order ID from `test_from` to `test_to` |
| `req_validate_orig(test)` | 1 | All | Validate original request for `test` |
| `eorder_count()` | 0 | All | Returns count of eOrder items |
| `eorder_string(index, field)` | 2 | All | Get string `field` from eOrder item at `index` |
| `eorder_txtheight()` | 0 | All | Get total text height of eOrder content |
| `eorder_subcount(index, type)` | 2 | All | Count sub-items of `type` in eOrder item at `index` |
| `eorder_substring(index, type, n)` | 3 | All | Get string from `n`th sub-item of `type` in eOrder item at `index` |
| `is_eorder()` | 0 | All | Returns 1 if request originated as an eOrder (SIR 64766) |
| `eorder_copyto()` | 0 | All | Get eOrder copy-to destination (SIR 64766) |
| `eorder_source()` | 0 | All | Get eOrder source system (SIR 82680) |

---

### Antibiotic and Sensitivity

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `get_ab_count()` | 0 | All | Count loaded antibiotic results |
| `get_ab_string(index)` | 1 | All | Get string representation of antibiotic at `index` |
| `get_ab_list(test, type, flags, options)` | 4 | All | Get list of antibiotics for `test` of `type` |
| `add_antibiotics(test)` | 1 | All | Add antibiotics to the result set for `test` |
| `antibiotic_id(index)` | 1 | All | Get antibiotic identifier at `index` |
| `antibiotic_test_method(test, antibiotic)` | 2 | All | Get testing method for `antibiotic` on `test` |
| `exp_ab_result(test, antibiotic, options)` | 3 | Test Recalc | Expand antibiotic result for `test`/`antibiotic` |

---

### GSI Generic Integration

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `gsi_status(id, field, options, index)` | 4 | Generic | Get GSI integration status `field` for `id` |
| `gsi_error_log(id, field, options)` | 3 | Generic | Get GSI error log entry `field` for `id` |
| `associate_crisp(sample)` | 1 | Generic | Associate current record with CRISP `sample` |
| `fetch_lab(labno)` | 1 | Generic | Fetch lab record by `labno` |
| `fetch_labur(labno, urno)` | 2 | Generic | Fetch lab/UR record pair `labno`/`urno` |
| `allocate_labno()` | 0 | Generic | Allocate a new lab number |
| `allocate_urno(type)` | 1 | Generic | Allocate a new UR number of `type` |
| `lab_processed(labno)` | 1 | Generic | Returns 1 if lab record `labno` has been processed |
| `test_on_lab(test)` | 1 | Generic | Returns 1 if `test` is on the loaded lab record |
| `get_storage_info(type)` | 1 | All | Get sample storage information of `type` |

---

### File and Document

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `include_mask(maskname)` | 1 | All | Include and execute another rule mask file `maskname`; max 32 levels deep |
| `loadfile(filename)` | 1 | All | Load and execute rule file `filename` |
| `duplicate_count(type)` | 1 | All | Count duplicate records of `type` |
| `add_extra_copy(type, destination)` | 2 | All | Add an extra copy of report of `type` to `destination` |
| `remove_extra_copy(type)` | 1 | All | Remove extra copy of `type` |
| `document_repaginate(type, options)` | 2 | All | Repaginate document of `type` |
| `get_file_lines()` | 0 | All | Get count of lines in the loaded file |
| `count_line_fields(line)` | 1 | All | Count fields on `line` (0-based) of the loaded file |
| `get_line_field(line, field)` | 2 | All | Get field at `field` (0-based) from file `line` (0-based) |
| `is_field_valid(line, field, type)` | 3 | All | Returns 1 if file field at `line`/`field` is valid/non-empty for `type` format |

---

### Signoff and Validation

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `signoff_report(options)` | 1 | All | Sign off the current report with `options` |
| `signoff_user(user, options)` | 2 | All | Get/set signoff user `user` with `options` |
| `privilege(priv)` | 1 | All | Returns 1 if current user has privilege `priv` |
| `level1_status(test, field)` | 2 | All | Get Level-1 validation status `field` for `test` |
| `ei_suitability_override(test)` | 1 | All | Override suitability check for EI on `test` |
| `ventana_update()` | 0 | All | Trigger Ventana instrument update (SIR 80659) |
| `add_labtm_spec_reqmt(test, reqmt)` | 2 | Test Recalc | Add lab TM special requirement `reqmt` for `test` |
| `add_urtm_spec_reqmt(test, reqmt)` | 2 | Test Recalc | Add UR TM special requirement `reqmt` for `test` |
| `add_urtm_antigen_neg_reqmt(reqmt)` | 1 | All | Add UR TM antigen-negative requirement `reqmt` |
| `rule3_diagnosis(type, n)` | 2 | Not Analyser | Get Rule-3 diagnosis entry `n` of `type` (SIR 78793) |
| `check_locnum()` | 0 | All | Validate location number on current record |

---

### Cancellation

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `cancelled_requests(type)` | 1 | All | Get list of cancelled requests of `type` |
| `cancelled_reason(index)` | 1 | All | Get cancellation reason for entry `index` |
| `cancelled_contact(index)` | 1 | All | Get contact for cancelled entry `index` |
| `cancelled_time(index)` | 1 | All | Get timestamp of cancellation entry `index` |
| `cancelled_user(index)` | 1 | All | Get user who cancelled entry `index` |
| `set_cancel_result_text(text)` | 1 | All | Set display `text` for a cancelled result |

---

### Utility and Misc

| Subroutine | Params | Eq Types | Description |
|---|---|---|---|
| `pdf_version(major, minor)` | 2 | All | Set PDF version for report output |
| `set_pdfmode(mode)` | 1 | All | Set PDF rendering `mode` (DEV-1807) |
| `bsr_send_rtf(test, result_idx, x, y, w, h)` | 6 | All | Send RTF document result of `test` at `result_idx` to BSR viewer at `(x,y)` size `(w,h)` |
| `bsr_send_button(label, action, x, y)` | 4 | All | Send a button with `label` and `action` to BSR viewer at `(x,y)` |
| `retrigger()` | 0 | Retrigger | Re-trigger the current equation (DEV-5812) |
| `eqlogging(level, message)` | 2 | All | Write `message` at severity `level` to the equation debug log (DEV-5812) |
| `note_field(type, field)` | 2 | All | Get `field` from the current note record of `type` |
| `add_testnote(test, text)` | 2 | All | Add note `text` to `test` result |
| `get_test_by_code(system, code)` | 2 | All | Get test/panel from coding system `system` and `code` |
| `get_code_by_sys_test(system, test)` | 2 | All | Get coding system `system` code for `test`/panel |
| `output_tm_alert(type)` | 1 | All | Output a transfusion medicine alert of `type` |

---

## Common Patterns

### Load and iterate cumulative results

```
loadcumulative(10, "SODIUM,POTASSIUM") ;
count = result_count(SODIUM) ;
i = 1 ;
while (i le count) {
    val = result_format(SODIUM, i, 1) ;
    i = i + 1 ;
}
```

### Conditional validation

```
if (SODIUM gt 150 or SODIUM lt 120) {
    validate_user(SODIUM, "SENIOR") ;
} else {
    validate(SODIUM) ;
}
```

### Include a shared mask

```
include_mask("common_checks") ;
```

### Output test name and result on a report (note: test is LAST)

```
output_testname(10, 50, 0, 9, 0, 9, "L", 0, 0, SODIUM) ;
output_results(80, 50, 0, 9, 0, 1, "R", "", 40, SODIUM) ;
output_units(130, 50, 0, 9, 0, 9, "L", SODIUM) ;
```

### Build a cumulative report block

```
loadcumulative(6, "GLUCOSE") ;
output_cumresults(10, 50, 0, 9, 0, 1, "R", "", 40, GLUCOSE) ;
```

### Check and set abnormal flag

```
if (HAEMOGLOBIN lt 80) {
    set_abnormal(HAEMOGLOBIN) ;
}
```

### Jump to a test in interactive mode

```
/* Only valid in Interactive equation types (TestRecalc, TestValidate, TestAccept, Request_Add, Request_Remove) */
if (SODIUM eq 0) {
    jump_to_test(SODIUM) ;
}
```

---

## Limitations

### Common misunderstandings

- **`delta(test)` is a boolean, not the delta amount** — it returns 1 if the system has already flagged the test as having breached its configured delta threshold (`TESTSTATUS_DELTA` bit). It does not return the numeric difference between current and previous results. To compute a delta manually, use `loadhistorical` and read `TEST[1]`, then subtract.
- **`test_present` and `test_ordered` are not the same** — `test_present` checks if a result slot exists in the lab (the test was added, auto-added, or reflexed); `test_ordered` checks if it was in the original request list. A reflexed test is present but not ordered; a cancelled test may be ordered but not present.
- **`result_count` takes a string, not a test identifier** — it parses a testlist string and returns the number of distinct tests in it (e.g. `result_count("SODIUM,POTASSIUM")` returns 2). It does not count loaded cumulative records for a test.
- **`test_modified` compares against the open-session snapshot** — it reflects changes made during the current editing session only. If a result was changed in a prior session and then saved, `test_modified` will return 0 because the snapshot was taken from the already-modified saved data.
- **`test_set/check/clear_status` only controls `<`/`>` qualifiers, nothing else** — despite the generic name, the `status` parameter only accepts `1` (`TESTSTATUS_LESS_THAN`, the `<` prefix) or `2` (`TESTSTATUS_GREATER_THAN`, the `>` prefix). Passing any other value to set/clear is silently ignored. These functions cannot set validated, abnormal, cancelled, or any other flag — use the dedicated subroutines for those (`set_abnormal`, `validate`, `suppress_result`, etc.).

### Language limitations

- **No functions / subroutine definitions** — you cannot define named procedures in equation code. Reuse is achieved via `include_mask()` which textually includes another mask's equation.
- **No recursion** — equations have no call stack beyond the operand stack; there is no way to call an equation from within itself.
- **No arrays of user variables** — user-defined variables are scalars or string. Indexed access (`var[i]`) only works for test identifiers and panel identifiers, not for arbitrary user-declared variables. (Exception: `varname[i]` in `OP_STORE_VAR_INDEXED` form does exist but behaviour is limited.)
- **No floating-point literals with exponents** — numeric literals must be plain integers or decimals; scientific notation (e.g. `1.5e-3`) is not supported.
- **`while` only, no `for`** — the only loop construct is `while`. A counter variable must be managed manually.
- **`exit` exits the whole equation** — there is no `break` or `continue` for early loop exit; `exit` terminates the entire equation immediately.
- **String comparison is case-sensitive** — `"A" = "a"` is false.

### Equation type restrictions

- **Report-only subroutines** — all `output_*` and `outcum_*` rendering functions are only valid in `EQTYPE_REPORT` (or `EQTYPE_REPDISP`). Calling them in TestRecalc or Interactive equations has no effect or will error at syntax check.
- **Interactive-only subroutines** — `jump_to_test()` and related UI-interaction subroutines are only valid in `ET_INTERACTIVE` types (TestRecalc, TestValidate, TestAccept, Request_Add, Request_Remove). The syntax checker rejects them in Report equations.
- **Analyser-only subroutines** — e.g. `set_analyser_flag()`. Restricted to `EQTYPE_ANALYSER`.
- **Billing-only subroutines** — `bill_*` functions require `EQTYPE_BILLING` or the billing licence. Using them in non-billing contexts is a syntax error.

### Data loading limitations

- **Only one load set at a time** — calling any `load*` function clears and replaces the existing `labarray`. You cannot hold two separately loaded sets simultaneously.
- **Maximum record count is capped** — `labarray` is a fixed-size static array (`MAX_LABARRAY = 500`). Requesting more than 500 records silently caps at 500.
- **`loadcumulative` result is undefined if `labdata` is null** — if there is no current lab record in context (e.g. in some Generic equations), load functions return 0 and `labarray` remains empty.
- **`loadhistorical` is validated-only; `loadcumulative` is not** — `loadhistorical` skips any lab record that has not been fully validated (`LHF_TESTS_VALIDATED`). `loadcumulative` includes all non-deleted records, even in-progress or unvalidated ones. If you use `loadhistorical` in a TestRecalc equation on a partially-entered lab, earlier same-session results may appear to be missing until they are validated.
- **`loadcumulative` only loads labs that have had a cumulative report printed** — it reads from the repcums file (report print history), not from the full UR lab list. A lab that has never triggered a cumulative report generation will not be returned by `loadcumulative`, even if it has validated results.
- **`loadhistorical` requires the current lab to already be in the UR record** — it locates the current `labno` in the UR lab list to use as a position anchor. If the lab is newly created and not yet saved to the UR record, the current labno will not be found and the function returns 0 records (or -1 error). This is a common cause of missing history in TestRecalc equations on new labs.
- **`loadcumulative` does not use the current lab as a position anchor** — it loads all records from the repcums file without filtering relative to the current record. This means it can include records that are chronologically after the current lab if they exist in the repcums file (e.g. future-dated labs, or previously printed labs from a later date).
- **Chronological ordering is based on registration/creation time, not collection time** — the UR lab list is ordered by the `time`/`time2` fields stored at registration. If a specimen was backdated or a lab was created out of sequence, its position in the loaded set may not match the actual collection order. `loadcumulative` sorts by collection date after loading, which partially mitigates this, but the initial set of records retrieved is still limited by the repcums file contents.
- **`loadhistorical` does not sort** — if you use `result_format(TEST, n, d)` after `loadhistorical`, index `n` corresponds to database retrieval order, not chronological order. Use `loadcumulative` if date-order matters.
- **`testlist` must be a string literal or variable** — the test list argument to load functions is a comma-separated string of test mnemonics (e.g. `"SODIUM,POTASSIUM"`), not a list of test identifiers.

### Output function limitations

- **Coordinate units are report-engine units** — x, y, width, height are not raw millimetres; they are in the report engine's internal unit system (hundredths of a mm in PostScript mode). Values that look correct in one report template may be wrong in another with a different page scale.
- **Font codes are system-configured integers** — there is no portable default; font `0` may render as the system default or may produce no output depending on configuration.
- **`output_*` functions write to the current output device** — if the output device is not set (e.g. no report is being rendered), output calls silently do nothing.
- **`outcum_*` functions require `loadcumulative` to have been called first** — they reference `labarray` directly; without a prior load call, they produce no output.

### HL7 command-rule limitations

- **HL7 command rules run per child segment, not per message.** A rule will be invoked once per PV1, once per PV2, once per IN1, once per OBR, once per OBX — with the unrelated pointers passed as null. Don't write a rule that assumes "I'll get all the OBRs together" — write it to handle one OBR at a time and idempotently across reruns.
- **The segment pointers are read-only from rule context for the wire data**, but the `lab_info`, `ur_info`, and `field_changes` structs are mutable and that's how rules influence downstream processing.
- **HL7 command rules don't dispatch via `EQTYPE_*` flags.** The dispatch is via the HL7 Command Rules table's per-event `process` flag and per-field `update_ur` / `update_lab` etc. — separate from the generic equation engine. The same equation language is used but the gating is different.
- **`add_ur` is per-event, not per-rule.** The rule engine doesn't decide whether to auto-create a patient — that's `cinfo->add_ur` (the command-rule row's per-event setting). Setting demographics in a rule on an A08 where `add_ur = 0` won't create a missing patient; the message will be rejected with `HL7N_UNKNOWN_PATIENT` before the rule runs.
- **A37 / A40 don't run command rules per-PID.** Patient unlink/merge events take a separate code path that doesn't iterate command rules; rules attached to A37/A40 events in the Command Rules row are unreachable.

### Analyser equation limitations

- **`EQTYPE_ANALYSER` runs on the LEVEL1_RESULT, not the validated lab.** The `lab->test[]` array isn't fully populated yet at this stage — `an_*` subroutines are the correct way to read the result-being-stored. Avoid touching lab-level state from analyser equations; use `EQTYPE_L1VALIDATE` or `EQTYPE_TESTRECALC` for that.
- **No UI subroutines** — `jump_to_test`, anything in the **Interactive** equation-type category, are inert in analyser context (there's no user session).
- **No report subroutines either** — `output_*` and `outcum_*` are inert in analyser context.
- **Per-driver context.** The analyser code passed to subroutines like `analyser(test)` is the **major code** from the driver registration (e.g. `ANALYSER_ACL`, `ANALYSER_STKS`), not the model code. Use the model field on `LEVEL1_RESULT` if you need model-level disambiguation.

---

## Operations and debugging

### Where rules live

Rule equations are stored in the **Rules** table in Evolution's configuration database. Each row contains the equation text, the equation type bitmask (`EQTYPE_*`), the scope (test, panel, lab group, etc.), and an active flag. Edits are reloaded by daemons on the next equation invocation — there's no hot-reload signal — but typically take effect within seconds.


### `include_mask` and shared rules

`include_mask("name")` textually includes another rule file. Common patterns:

- **Shared validation rules** — put cross-test logic in a mask, include it from every test's TestRecalc rule.
- **Site-wide defaults** — a base mask that every report includes for the header/footer.
- **Conditional inclusion** — include a mask only when a flag is set, so the textual replacement carries the conditional through.

Limits:

- Max include depth is 32 levels — recursion isn't supported and depth is bounded to prevent runaway inclusion.
- Names are global — masks live in a shared namespace. Pick distinct names for site-specific masks to avoid collisions with stock masks.

### Logging from within an equation

[`eqlogging(level, message)`](#utility-and-misc) writes to the equation debug log. Use it for breadcrumbs when debugging rule execution.

Levels:

- `0` — informational
- `1` — warning
- `2` — error

The log file location is configured per-instance via the standard daemon log pipeline. Look for `eq_log` or the equation-engine log adjacent to other daemon logs in `%a/`.

`eqlogging` is cheap when not actually writing — but don't leave high-frequency `eqlogging(0, ...)` calls in production rules; they accumulate volume quickly.

### Validating syntax

Rule equations are syntax-checked when the row is saved through the Rules editor. The syntax checker:

- Validates subroutine names against the dispatch table.
- Checks parameter counts match the subroutine signature.
- Verifies equation-type compatibility (e.g. rejects `jump_to_test` in a `EQTYPE_REPORT` rule).
- **Does NOT** validate that test/panel names exist — a typo in a test mnemonic compiles cleanly but evaluates to 0 at runtime.

After saving, run a smoke test against a representative record before enabling broadly.

### Common diagnostic flow

When a rule isn't doing what you expect:

1. **Confirm the rule is active.** Check the active flag on the rule row.
2. **Confirm the equation type matches the trigger.** A rule with only `EQTYPE_REPORT` set won't fire during result entry. A rule expecting to run from inbound HL7 needs to be configured in the HL7 Command Rules table, not the generic Rules table.
3. **Confirm the scope matches.** Tests are matched by scope filter (test, panel, lab group, request); a rule scoped to lab group A won't fire for a lab group B record even if all other conditions match.
4. **Add `eqlogging` breadcrumbs.** `eqlogging(0, "reached point A")` before and after critical branches isolates which path the rule actually took.
5. **For HL7 command rules,** verify the matching `cmd_type` in the HL7 Command Rules row matches the inbound message family (ADT=0, ORU=1, ORM=2, OML=3).
6. **For analyser rules,** check that `EQTYPE_ANALYSER` is set; rules without it won't run from the driver path even if their other types match.
7. **For `loadcumulative` / `loadhistorical` issues,** verify the patient has the data you expect — `loadcumulative` requires labs to have been on a cumulative report; `loadhistorical` requires labs to be validated AND the current lab to be in the UR list (see [Data Loading](#data-loading) for details).

### Tracing the rule path

To trace which rules fired on a given event:

- Equation-engine log (when enabled) records every rule entry/exit.
- For HL7 inbound, the receive daemon logs `hl7n_rules_process` invocations at `HL7N_ERROR_LOGGING` level when `CFG_ERROR_LOGGING_ENABLE` is on.
- For analysers, the driver log (`%a/<mnem>_log`) shows result reception; equation execution against the L1 result is logged separately via the equation engine.

### Disabling rules

Rules can be disabled by:

1. **Active flag off** — cleanest, leaves the rule for later reactivation.
2. **`exit;` at the top of the equation** — quickly disables without losing the body.
3. **Renaming the test/panel scope** — narrows the rule out of the current path.

Don't delete rules unless you're certain they're unused; they often have implicit dependencies (cross-rule data flow via shared user variables, or chain-validations that break silently).

---

*Generated from `src/eq.c` RWF dispatch table (lines 19144–19511). Total subroutines: ~352. Parameter details sourced from `function_0()` through `function_13()` implementations and inline comments.*
