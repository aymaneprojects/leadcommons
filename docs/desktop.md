# The desktop app

LeadCommons installs on your computer like any other application: an icon in the Start menu,
the Dock or your app list, its own window, a real menu bar, notifications, and the usual
"Save as" window for exports. No terminal, no account, nothing to pay. Your data stays on your
computer, in the `.leadcommons` folder of your home folder.

It is built the way Slack, Visual Studio Code or Notion are built: an Electron shell draws the
window, menus and dialogs, and the LeadCommons engine runs inside the app, out of sight. It
only listens on your own computer (127.0.0.1); nothing is reachable from the network.

Download the file for your computer from the
[latest release](https://github.com/aymaneprojects/leadcommons/releases/latest):

| Computer | File |
| --- | --- |
| Windows 10 or 11 (64-bit) | `LeadCommons-<version>-windows-x64-setup.exe` |
| Mac with Apple Silicon (M1 or newer) | `LeadCommons-<version>-macos-arm64.dmg` |
| Ubuntu, Debian, Linux Mint (64-bit) | `LeadCommons-<version>-linux-amd64.deb` |
| Other Linux (64-bit) | `LeadCommons-<version>-linux-x86_64.AppImage` |

On a Mac with an Intel processor, use the one-line installer in [install.md](install.md).

## Install

**Windows.** Run the setup file. It installs for your user only (no administrator rights) and
adds LeadCommons to the Start menu and the desktop. Windows may show "Windows protected your PC"
because the app is not signed with a paid certificate yet: click **More info**, then **Run
anyway**.

**macOS.** Open the `.dmg` and drag LeadCommons into Applications. The first time, macOS says it
cannot check the app because it is not notarised by Apple. Open **System Settings > Privacy &
Security**, scroll down and click **Open Anyway** next to LeadCommons. You only do this once. If
you prefer the terminal: `xattr -dr com.apple.quarantine /Applications/LeadCommons.app`.

**Ubuntu, Debian, Mint.** Open the `.deb` with your software installer, or:

```bash
sudo apt install ./LeadCommons-*-linux-amd64.deb
```

**Other Linux.** Make the AppImage executable and run it:

```bash
chmod +x LeadCommons-*-linux-x86_64.AppImage
```

```bash
./LeadCommons-*-linux-x86_64.AppImage
```

If it closes at once on a recent Ubuntu (a security setting for AppImages), start it with
`--no-sandbox`, or use the `.deb`.

## What you get

- **Menus**: File (new searches, import a CSV, update the company data, settings, quit),
  Edit, View (zoom, full screen), Go (back, forward, and every section with Ctrl/Cmd+1 to 9),
  Window and Help (documentation, report a problem, open the data folder or the log, check
  for updates). In French when your computer is in French.
- **Exports** open the normal "Save as" window; a notification tells you when the file is saved.
- **Background jobs** (finding emails, downloading data) show on the Dock or taskbar icon, and a
  system notification tells you when one finishes while you are in another app.
- **One app at a time**: opening it again brings the open window to the front. The window
  keeps its size and position.
- **Updates**: the app tells you when a new version is published and opens its download page.
- **Links to other websites** open in your usual browser; the app window only shows LeadCommons.

## First start

The app comes with a small sample of companies, so it works at once. At the first start it also
downloads the full company pack (a few minutes, shown in the jobs menu at the top). Search
switches to it as soon as the download is done. Later, **File > Update the company data** gets the
monthly update.

## Good to know

- The app and the command-line install share the same data folder, so you can use both.
- Logs are in `.leadcommons/desktop.log` (**Help > Open the log file**), useful when you report a
  problem.
- To uninstall: Windows, **Settings > Apps**. macOS, drag the app to the Bin. Linux,
  `sudo apt remove leadcommons-desktop` or delete the AppImage. Your data folder stays; delete
  `.leadcommons` too to remove everything.
- Dictation: use your computer's own dictation in the message box (Windows key + H on
  Windows, Fn twice on a Mac).
- Signing: the Windows and macOS builds are not signed with a paid certificate yet, which is
  why the systems warn you. The code is open and the builds are made in public on GitHub Actions
  from this repository; each release lists the SHA-256 checksums in `SHA256SUMS-desktop.txt`.

## How the builds are made

`.github/workflows/desktop.yml`, on Windows, macOS and Linux machines:

1. freezes the Python engine with PyInstaller (`packaging/desktop/leadcommons.spec`) and checks
   that it serves its pages (`packaging/desktop/smoke.sh`);
2. builds the Electron app around it with electron-builder (`desktop/electron/`);
3. starts the real app (the installed one on Windows, the one inside the DMG on macOS, the
   AppImage on Linux) in test mode: it opens the main screens, checks them, saves screenshots
   of what the window shows, and quits;
4. attaches the installers to the release.

To work on the shell: build the engine once (`pyinstaller packaging/desktop/leadcommons.spec`),
then in `desktop/electron` run `npm ci` and `npm start` (set `LEADCOMMONS_BACKEND` to use another
engine binary).
