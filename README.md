> **Archived.** Replaced by [webcli](https://github.com/truefrontier/webcli). Install: `brew install truefrontier/tap/webcli` or `pipx install truefrontier-webcli`. The equivalent command is now `webcli loom`.

# loom-cli

Unofficial read-only CLI for [Loom](https://www.loom.com). Sign in once in Chrome. Then list your videos and pull official transcripts.

Built by [True Frontier](https://truefrontierapps.com). Not affiliated with Loom or Atlassian.

The session stays on your machine, in a local Chrome profile. This tool does not print cookies, and it does not ship a cloud login.

## Requirements

- Python 3.10+
- Google Chrome

## Install

```bash
pipx install git+https://github.com/truefrontier/loom-cli.git
```

Or from a checkout:

```bash
pipx install .
```

Playwright uses your installed Chrome (`channel="chrome"`). You do not need `playwright install chromium`.

## Commands

```bash
loom login
loom list
loom list --limit 20
loom list --id-only
loom list --compact | jq
loom list --select id,title --csv
loom info <id>
loom info <id> --format json --compact
loom transcript <id>
loom transcript <id> --format json
loom transcript <id> -o out.txt
```

`loom login` opens Chrome. Sign in, wait until your library loads, and the window closes. The profile is saved at `~/.loom-cli/chrome-profile/`.

If a command says the session expired, run `loom login` again.

## How it works

After login, the CLI calls the same GraphQL operations the Loom library uses (`GetLoomsForLibrary`, `GetVideoCardDetails`) from a signed-in Chrome profile. Transcripts are the official phrase file the share page already requests.

Read-only. It cannot upload, edit, delete, or share videos.


## Agent output

Inspired by [CLI Printing Press](https://github.com/mvanhorn/cli-printing-press) agent UX:

- Piped stdout auto-uses JSON when `--format` is `table` (no `--json` needed)
- `--compact` for high-gravity fields only
- `--select id,title` to project JSON/CSV fields
- `--csv` for spreadsheet-friendly rows
- `--quiet` / `-q` to suppress status lines
- Exit codes: `0` ok, `2` usage, `3` not found, `4` auth, `5` api/runtime

## Limits

- Your library only. No public search.
- Transcripts exist only when Loom has generated one.
- A first page of the library is 12 videos. `loom list` follows the cursor until it hits your `--limit`, or until the library ends.
