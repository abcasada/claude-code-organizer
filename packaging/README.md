# Packaging

Release artifacts are built by `.github/workflows/release.yml`; these notes are
for building them by hand. Output goes to `dist/` (PyInstaller also uses
`build/pyinstaller/`); both are scratch directories.

The version comes from `__version__` in `src/claude_code_organizer/__init__.py`.

## Windows: `dist/ClaudeCodeOrganizer.exe`

Single-file windowed exe built with PyInstaller. The `.ico` and version
resource are generated from `icon.svg` and `__version__` at build time.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e . pyinstaller
.venv\Scripts\python packaging\windows\build_exe.py
```

`dist\ClaudeCodeOrganizer.exe --smoke-test` exits 0 if the bundle works (it has
no console, so check `$LASTEXITCODE` / `Start-Process -Wait -PassThru`).

## Debian/Ubuntu: `dist/claude-code-organizer_<version>_all.deb`

Architecture-independent package that depends on the distribution's PySide6
(`python3-pyside6.*`, tested on Ubuntu 26.04). Building needs only `python3`
and `dpkg-deb`:

```sh
python3 packaging/linux/build_deb.py
sudo apt install ./dist/claude-code-organizer_*_all.deb
QT_QPA_PLATFORM=offscreen claude-code-organizer --smoke-test
```

To build from another OS, use a container:

```sh
docker run --rm -v "$PWD:/src" -w /src ubuntu:26.04 \
  sh -c 'apt-get update && apt-get install -y python3 && python3 packaging/linux/build_deb.py'
```

## Cutting a release

1. Bump `__version__` in `src/claude_code_organizer/__init__.py` **and**
   `version` in `pyproject.toml` (the release workflow fails if they or the
   tag disagree). Commit and push to `main`; wait for CI to pass.
2. Tag and push:

   ```sh
   git tag v0.2.0
   git push origin v0.2.0
   ```

3. The release workflow builds and smoke-tests both artifacts, then creates the
   GitHub release `v0.2.0` with generated notes and the `.exe` and `.deb`
   attached. Edit the notes on GitHub afterwards if needed.

If a release run fails, fix the problem, delete the tag
(`git push --delete origin v0.2.0 && git tag -d v0.2.0`) and tag again.
