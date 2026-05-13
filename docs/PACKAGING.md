# Rule Lint — Packaging & Distribution

How to build standalone Rule Lint executables for macOS and Windows 11 that
end-users can run on a clean machine (no Python, no Evolution source tree).

The CLI tool lives in [../rule_lint.py](../rule_lint.py). This
document covers the **GUI version** at [../rule_lint_gui.py](../rule_lint_gui.py),
how to bundle it, and how to ship.

For the linter's behaviour and CLI options, see [USER_GUIDE.md](USER_GUIDE.md).

---

## Architecture

| Layer | File | Notes |
|---|---|---|
| GUI | [`../rule_lint_gui.py`](../rule_lint_gui.py) | Tkinter window, stdlib-only |
| CLI / engine | [`../rule_lint.py`](../rule_lint.py) | Imported as a module by the GUI |
| Catalogue | [`../rule_catalogue.py`](../rule_catalogue.py) | Auto-generated from `src/eq.c` |
| Generator | [`../gen_rwf_catalogue.py`](../gen_rwf_catalogue.py) | Run when `src/eq.c` changes |
| PyInstaller spec | [`../rule_lint.spec`](../rule_lint.spec) | Reproducible build config |
| Build helper | [`../build_release.py`](../build_release.py) | Wraps the spec + regenerates catalogue |
| CI workflow | [`.github/workflows/build_rule_lint.yml`](.github/workflows/build_rule_lint.yml) | Builds macOS + Windows binaries on tag |

The end-user binary is a single executable (Windows) or app bundle (macOS)
that includes a frozen Python runtime plus the linter source. There are no
external runtime dependencies — tkinter is part of the standard library.

---

## Building locally

### Prerequisites

| Platform | Need |
|---|---|
| **macOS** | Python 3.11+ from python.org or `brew install python@3.11`. **Avoid the system Python in `/usr/bin/python3`** — its bundled tkinter has rendering issues on Sonoma+. |
| **Windows 11** | Python 3.11+ from python.org. Tick "tcl/tk and IDLE" in the installer. |
| **Both** | `pip install pyinstaller` (latest is fine, 6.x tested) |

PyInstaller does **not cross-compile** — you have to build the macOS binary on
a Mac and the Windows binary on Windows. CI handles this automatically (see
below).

### Build steps

```bash
# Macos / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install pyinstaller

python3 ../build_release.py
# Output: dist/RuleLint.app (macOS) or dist/RuleLint (Linux)
```

```powershell
# Windows
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install pyinstaller

python support\build_release.py
# Output: dist\RuleLint.exe
```

The first build takes ~30-60 seconds and produces a 12-20 MB binary
depending on platform. Subsequent builds are faster (PyInstaller caches the
analysis).

### What `build_release.py` does

1. Checks for PyInstaller.
2. Regenerates `rule_catalogue.py` from `src/eq.c` if `src/eq.c` is newer.
   Skipped if `src/eq.c` isn't present (i.e. you're building from a tarball
   that ships the catalogue but not the engine source).
3. Cleans the `build/` and `dist/` directories.
4. Invokes PyInstaller with the spec file.
5. Prints the output paths and sizes.

### Smoke test

```bash
# macOS
open dist/RuleLint.app
# A window should appear. Pick a sample rule file, click "Run Lint".

# Windows
.\dist\RuleLint.exe
# Same — the GUI should open.

# Linux (manual builds; not officially distributed)
./dist/RuleLint
```

---

## CI / automated builds

The workflow at [`.github/workflows/build_rule_lint.yml`](.github/workflows/build_rule_lint.yml)
builds binaries for **macOS** and **Windows** in parallel.

### Triggers

| Trigger | When |
|---|---|
| `push` of tag `rule-lint-v*` | Tagged release — uploads to GitHub Release |
| `workflow_dispatch` | Manual run from the Actions tab |

### To cut a release

```bash
git tag rule-lint-v1.0
git push --tags
```

Two GitHub Actions jobs start automatically. Each:

1. Checks out the repo
2. Installs Python 3.11 and PyInstaller
3. Runs `../build_release.py`
4. Smoke-tests the produced binary
5. Uploads as a workflow artifact
6. **If the trigger was a tag**, also attaches `RuleLint-macos.zip` and
   `RuleLint.exe` to the corresponding GitHub Release

After ~5-10 minutes you have downloadable binaries on the Release page.

### Artifacts you get

| File | Platform | Size |
|---|---|---|
| `RuleLint.app/` (zipped as `RuleLint-macos.zip`) | macOS | ~18 MB compressed |
| `RuleLint.exe` | Windows | ~15 MB |

Both bundle Python 3.11, tkinter, the linter, and the catalogue — no install
required by the end user.

---

## Distribution

Three increasingly polished options, depending on how much you want to
invest:

### Option 1: Plain ZIP / EXE (minimum effort, what CI produces today)

- **Pro:** zero infrastructure, works immediately
- **Con:** users see scary warnings ("unidentified developer" on macOS,
  SmartScreen on Windows)

Users get past the warning by:

- **macOS:** right-click → Open → confirm once. Or
  `xattr -d com.apple.quarantine RuleLint.app` from Terminal.
- **Windows:** click "More info" then "Run anyway" on the SmartScreen prompt.

Acceptable for internal/lab use; not great for unfamiliar end-users.

### Option 2: Code-signed binaries (recommended for wider distribution)

Removes the "unidentified developer" warnings. Requires paid certificates:

- **macOS:** Apple Developer Program ($99/year) + Developer ID Application
  certificate. Then sign + notarise:
  ```bash
  codesign --deep --force --verify --verbose \
      --sign "Developer ID Application: Your Name (TEAMID)" \
      --options runtime \
      dist/RuleLint.app
  ditto -c -k --keepParent dist/RuleLint.app RuleLint.zip
  xcrun notarytool submit RuleLint.zip --keychain-profile "AC_PASSWORD" --wait
  xcrun stapler staple dist/RuleLint.app
  ```
- **Windows:** Code-signing certificate from a CA (DigiCert, Sectigo, etc.,
  $200-600/year). Then sign:
  ```powershell
  signtool sign /a /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\RuleLint.exe
  ```

The CI workflow has comment placeholders for these steps — add the secrets
and uncomment.

### Option 3: Native installers (DMG / MSI, optional polish)

- **macOS:** package as a DMG with `create-dmg`:
  ```bash
  brew install create-dmg
  create-dmg \
      --volname "Rule Lint" \
      --window-size 540 360 \
      --icon "RuleLint.app" 130 150 \
      --app-drop-link 410 150 \
      RuleLint.dmg dist/RuleLint.app
  ```
- **Windows:** MSI via WiX or NSIS. Out of scope for the default workflow;
  build a `.msi` after PyInstaller produces `.exe`.

Most lab/internal distributions don't need installers — a zipped `.app` and
a `.exe` are fine. Add Option 3 only when you're shipping to customers who
expect a "real" installer.

---

## What's in the bundle

The PyInstaller spec at [`../rule_lint.spec`](../rule_lint.spec)
controls bundling. Bundled contents:

- Frozen Python 3.11 runtime
- `rule_lint_gui.py` as the entry point
- `rule_lint.py` as a hidden import (the linter engine)
- `rule_catalogue.py` as a data file *and* hidden import (Python sometimes
  resolves it via different paths depending on freeze method)
- `tkinter` and its sub-modules
- A trimmed standard library — `test`, `unittest`, `lib2to3`, `pydoc_data`,
  `setuptools`, `pip`, `wheel` are excluded
- Heavy science/UI libraries explicitly excluded so PyInstaller doesn't
  accidentally pull them in: numpy, pandas, matplotlib, scipy, PIL, PyQt*,
  PySide*

Total size: ~15-20 MB. Could be smaller with UPX compression (enable in the
spec) but the trade-off is slower startup and occasional antivirus
false-positives. We leave UPX off.

### macOS-specific

The `.app` bundle is configured to register `.eq`, `.rule`, and `.mask` as
openable document types. On macOS, right-click on one of those files → Open
With → Rule Lint. The path is passed to the GUI on launch and pre-populates
the file picker.

`NSHighResolutionCapable: True` is set so the window looks correct on Retina
displays.

### Windows-specific

`console=False` in the spec means no Command Prompt window flashes when the
user double-clicks the `.exe`. If you need diagnostic output, build with
`console=True` temporarily.

---

## Updating the binary

Two layers of "update":

### When `src/eq.c` changes (new subroutines, etc.)

The catalogue must be regenerated and the binary rebuilt:

```bash
python3 ../gen_rwf_catalogue.py     # regenerate catalogue
python3 ../build_release.py         # rebuild executable
```

In CI this happens automatically — every workflow run regenerates the
catalogue from the current `src/eq.c` before bundling.

### When the GUI / linter code changes

Just rebuild — `build_release.py` re-runs PyInstaller:

```bash
python3 ../build_release.py
```

### Versioning

The displayed version comes from `APP_VERSION` in
[`../rule_lint_gui.py`](../rule_lint_gui.py). Bump it in lockstep
with the release tag.

For automated version stamping from the tag, add to the workflow before the
build step:

```yaml
- name: Stamp version from tag
  if: startsWith(github.ref, 'refs/tags/')
  shell: bash
  run: |
      VER="${GITHUB_REF_NAME#rule-lint-v}"
      sed -i.bak "s/APP_VERSION = \".*\"/APP_VERSION = \"$VER\"/" ../rule_lint_gui.py
```

---

## End-user instructions (what to put on the download page)

Cut-and-paste-able for a wiki or README:

### macOS

1. Download `RuleLint-macos.zip` from the release page.
2. Double-click to expand. Drag `RuleLint.app` to `/Applications`.
3. The first time you run it, **right-click** → **Open** → confirm the
   prompt. (This step is only needed if the build isn't code-signed.)
4. To lint a rule file: drag it onto the dock icon, or click **Browse…** in
   the app.

### Windows 11

1. Download `RuleLint.exe` from the release page.
2. Place it anywhere — e.g. your Desktop or a `C:\Tools\` folder.
3. Double-click to launch. If SmartScreen warns about an unknown publisher,
   click **More info** → **Run anyway** (only needed if not code-signed).
4. To lint a rule file: click **Browse…** in the app.

### Both platforms

- **Optional but recommended:** export your site's test/panel catalogue as
  CSV (one mnemonic per row, header `mnem`) and load it via the "Test
  catalogue (CSV)" field in the app. This enables `UPPER001` and `TEST001`
  checks. Without it, those checks are disabled to avoid false positives on
  legitimate test result writes like `SODIUM = 5;`.
- For shared rule mask resolution, add each mask directory to the
  "Include paths" list. The app recursively lints `include_mask()` targets
  inside each rule.

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'rule_catalogue'"

The catalogue wasn't bundled. Causes:

- `../rule_catalogue.py` didn't exist when the build ran. Run
  `python3 ../gen_rwf_catalogue.py` first.
- You modified `rule_lint.spec` and removed the `datas` entry. Restore it.

### Tkinter rendering issues on macOS

If text looks crushed or fonts are wrong, you're probably building against
the system Python's bundled Tk 8.6. Install Python from python.org (which
ships a newer Tk) and rebuild.

### Windows: "Windows protected your PC" SmartScreen

Expected for unsigned binaries. Either code-sign (Option 2 above) or tell
users to click "More info" → "Run anyway."

### macOS: "App is damaged and can't be opened"

Quarantine attribute applied by Safari/AirDrop. Clear it:

```bash
xattr -dr com.apple.quarantine /Applications/RuleLint.app
```

Or, better: code-sign + notarise so the OS trusts the bundle natively.

### The binary is enormous (50+ MB)

Something heavy got pulled in. Run:

```bash
pyinstaller --onefile --noconfirm ../rule_lint_gui.py 2>&1 | grep "Hidden import"
```

…to see what's being included. Add unwanted imports to the `excludes` list
in `rule_lint.spec`.

### CI build fails on `actions/upload-artifact@v4`

The action requires Node 20. Older self-hosted runners may need updating.

### App opens but Run Lint does nothing

Make sure the file you picked exists and is readable. Check the GUI's status
bar — errors are surfaced there and via dialog boxes. For deeper debugging,
rebuild with `console=True` in `rule_lint.spec` to see Python tracebacks.

---

## Roadmap (deliberately out of scope for now)

- **Auto-update mechanism** — the app could poll for new releases. Requires
  hosting + signature verification.
- **Drag-and-drop file open** — currently file picker only. Adding requires
  the optional `tkdnd` library, which complicates packaging.
- **Settings persistence** — remember last `--testlist`, `--eqtype`,
  include paths between launches. Store in a per-user config file.
- **Linux AppImage** — straightforward extension of the workflow with a
  matrix entry for `ubuntu-latest`. Not requested in the current scope but
  trivially achievable.
- **Notarised + DMG-packaged macOS release** — needs Apple Developer
  account; CI YAML has commented placeholders.
- **MSI installer for Windows** — needs WiX tools; the workflow can be
  extended.

---

## Related references

- [USER_GUIDE.md](USER_GUIDE.md) — the CLI user guide and complete option
  reference
- [LANGUAGE_REFERENCE.md](LANGUAGE_REFERENCE.md) — the rule
  language reference
- [`../rule_lint.py`](../rule_lint.py) — the linter engine source
- [`../rule_lint_gui.py`](../rule_lint_gui.py) — the GUI source
