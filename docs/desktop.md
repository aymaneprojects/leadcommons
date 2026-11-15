# The desktop app

LeadCommons runs on your computer as a normal app: double-click it and it opens in its own
window. No terminal, no account, nothing to pay. Your data stays on your computer, in the
`.leadcommons` folder of your home folder.

Download the file for your computer from the
[latest release](https://github.com/aymaneprojects/leadcommons/releases/latest):

| Computer | File |
| --- | --- |
| Windows 10 or 11 (64-bit) | `LeadCommons-<version>-windows-x64-setup.exe` |
| Mac with Apple Silicon (M1 or newer) | `LeadCommons-<version>-macos-arm64.dmg` |
| Linux (64-bit) | `LeadCommons-<version>-linux-x86_64.AppImage` |

On a Mac with an Intel processor, use the one-line installer in [install.md](install.md).

## Install

**Windows.** Run the setup file. It installs for your user only, so no administrator rights
are needed. Windows may show "Windows protected your PC" because the app is not signed with a
paid certificate yet: click **More info**, then **Run anyway**.

**macOS.** Open the `.dmg` and drag LeadCommons into Applications. The first time, macOS says
it cannot check the app because it is not notarised by Apple. Open **System Settings > Privacy &
Security**, scroll down and click **Open Anyway** next to LeadCommons. You only do this once.
If you prefer the terminal: `xattr -dr com.apple.quarantine /Applications/LeadCommons.app`.

**Linux.** Make the file executable and run it:

```bash
chmod +x LeadCommons-*-linux-x86_64.AppImage
```

```bash
./LeadCommons-*-linux-x86_64.AppImage
```

On Linux the app opens in your browser and shows a small window with **Open** and **Quit**.

## First start

The app comes with a small sample of companies, so it works at once. At the first start it
also downloads the full company pack (a few minutes, shown in the jobs menu at the top). Search
switches to it as soon as the download is done. Later, **Settings > Data packs > Download the
newest pack** gets the monthly update.

## Good to know

- The app only listens on your own computer (127.0.0.1). Nothing is reachable from the network.
- The app and the command-line install share the same data folder, so you can use both.
- Logs are in `.leadcommons/desktop.log`, useful when you report a problem.
- To uninstall: Windows, use **Settings > Apps**. macOS, drag the app to the Bin. Linux, delete
  the file. Your data folder stays; delete `.leadcommons` too to remove everything.
- Signing: the Windows and macOS builds are not signed with a paid certificate yet, which is
  why the systems warn you. The code is open and the builds are made in public on GitHub
  Actions from this repository; each release lists the SHA-256 checksums in
  `SHA256SUMS-desktop.txt`.

## Run the window from a command-line install

```bash
uv tool install --force "leadcommons[desktop] @ git+https://github.com/aymaneprojects/leadcommons"
```

```bash
leadcommons desktop
```

## How the builds are made

`.github/workflows/desktop.yml` freezes the app with PyInstaller (`packaging/desktop/`) on
Windows, macOS and Linux runners, starts each build without a window to check that it serves its
pages (`packaging/desktop/smoke.sh`), installs the Windows setup silently and tests it again,
then attaches the files to the release.
