"""Unofficial read-only CLI for Loom. Session stays in the local Chrome profile."""

import asyncio
import json
import re
from pathlib import Path

import click
from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

PROFILE = Path.home() / ".loom-cli" / "chrome-profile"
QUERIES = Path(__file__).resolve().parent / "queries"
console = Console()
ID_RE = re.compile(r"[0-9a-f]{32}")
STEALTH = ["--disable-blink-features=AutomationControlled"]


def has_session() -> bool:
    return PROFILE.exists() and any(PROFILE.iterdir())


def need_session() -> None:
    if not has_session():
        console.print("[red]No session. Run: loom login[/red]")
        raise SystemExit(1)


def video_id(value: str) -> str:
    match = ID_RE.search(value.strip())
    if not match:
        raise click.BadParameter("expected a Loom video id or share URL")
    return match.group(0)


def load_query(name: str) -> dict:
    path = QUERIES / f"{name}.json"
    if not path.exists():
        raise RuntimeError(f"Missing bundled query {name}")
    return json.loads(path.read_text())


def fmt_duration(seconds) -> str:
    if seconds is None:
        return ""
    total = int(round(float(seconds)))
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


async def open_context(playwright, headless: bool):
    return await playwright.chromium.launch_persistent_context(
        str(PROFILE),
        headless=headless,
        channel="chrome",
        args=STEALTH,
    )


async def graphql(page, operation: str, variables: dict) -> dict:
    doc = load_query(operation)
    payload = {
        "operationName": operation,
        "query": doc["query"],
        "variables": variables,
    }
    result = await page.evaluate(
        """async (payload) => {
          const res = await fetch("https://www.loom.com/graphql", {
            method: "POST",
            credentials: "include",
            headers: {
              "content-type": "application/json",
              "accept": "application/json",
              "apollographql-client-name": "web",
              "x-loom-request-source": "loom_web"
            },
            body: JSON.stringify(payload)
          });
          let body = null;
          try { body = await res.json(); } catch (e) { body = { error: "non-json" }; }
          return { status: res.status, body };
        }""",
        payload,
    )
    if result.get("status") != 200 or not isinstance(result.get("body"), dict):
        raise RuntimeError(
            f"{operation} failed (HTTP {result.get('status')}). Session may be expired. Run: loom login"
        )
    body = result["body"]
    if body.get("errors"):
        raise RuntimeError(f"{operation} failed. Session may be expired. Run: loom login")
    return body


async def ensure_origin(page) -> None:
    if "loom.com" not in (page.url or ""):
        await page.goto("https://www.loom.com/looms/videos", wait_until="domcontentloaded")
    if "login" in page.url or "signup" in page.url:
        raise RuntimeError("Session expired. Run: loom login")


async def login() -> None:
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    console.print("Opening Chrome. Sign in to Loom. This window closes once your library loads.")
    async with async_playwright() as p:
        context = await open_context(p, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.loom.com/login", wait_until="domcontentloaded")
        for _ in range(180):
            await asyncio.sleep(5)
            urls = []
            for pg in context.pages:
                try:
                    urls.append(pg.url)
                except Exception:
                    continue
            if any(
                "loom.com" in u
                and "login" not in u
                and "signup" not in u
                and "accounts.google." not in u
                and any(bit in u for bit in ("/looms", "/library", "/share/", "/settings"))
                for u in urls
            ):
                await context.close()
                console.print("[green]Session saved.[/green]")
                return
        await context.close()
        raise RuntimeError("Login timed out. Run: loom login")


async def list_videos(limit):
    doc = load_query("GetLoomsForLibrary")
    base = dict(doc.get("variables") or {})
    videos = []
    cursor = None
    async with async_playwright() as p:
        context = await open_context(p, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.loom.com/looms/videos", wait_until="domcontentloaded")
        await ensure_origin(page)
        while True:
            variables = dict(base)
            variables["cursor"] = cursor
            body = await graphql(page, "GetLoomsForLibrary", variables)
            connection = body.get("data", {}).get("getLooms", {}).get("videos", {})
            for edge in connection.get("edges") or []:
                node = edge.get("node") or {}
                if not node.get("id"):
                    continue
                videos.append(
                    {
                        "id": node.get("id"),
                        "title": node.get("name") or "",
                        "visibility": node.get("visibility") or "",
                    }
                )
                if limit and len(videos) >= limit:
                    await context.close()
                    return videos
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
                break
            cursor = page_info.get("endCursor")
        await context.close()
    return videos


async def video_info(vid: str) -> dict:
    doc = load_query("GetVideoCardDetails")
    variables = dict(doc.get("variables") or {})
    variables["videoId"] = vid
    async with async_playwright() as p:
        context = await open_context(p, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.loom.com/looms/videos", wait_until="domcontentloaded")
        await ensure_origin(page)
        body = await graphql(page, "GetVideoCardDetails", variables)
        await context.close()
    video = (body.get("data") or {}).get("video") or {}
    if not video or video.get("__typename") in {"GenericError", "UserNotAuthorizedError"}:
        raise RuntimeError(f"No video {vid}. Session may be expired. Run: loom login")
    owner = video.get("owner") or {}
    name = owner.get("display_name") or " ".join(
        x for x in (owner.get("first_name"), owner.get("last_name")) if x
    )
    return {
        "id": video.get("id") or vid,
        "title": video.get("name") or "",
        "created_at": video.get("createdAt"),
        "duration_seconds": video.get("playable_duration"),
        "views": (video.get("views") or {}).get("total"),
        "owner": name,
        "complete": video.get("complete"),
        "archived": video.get("archived"),
        "url": f"https://www.loom.com/share/{vid}",
    }


async def transcript_phrases(vid: str) -> list:
    async with async_playwright() as p:
        context = await open_context(p, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()
        seen = asyncio.get_event_loop().create_future()

        async def on_response(response):
            url = response.url
            if "mediametadata/transcription/" not in url or vid not in url:
                return
            if seen.done():
                return
            try:
                data = await response.json()
            except Exception:
                return
            if not seen.done():
                seen.set_result(data)

        page.on("response", on_response)
        await page.goto(f"https://www.loom.com/share/{vid}", wait_until="domcontentloaded")
        if "login" in page.url:
            await context.close()
            raise RuntimeError("Session expired. Run: loom login")
        try:
            data = await asyncio.wait_for(seen, timeout=25)
        except TimeoutError as exc:
            await context.close()
            raise RuntimeError(f"No official transcript for {vid}") from exc
        await context.close()
    phrases = []
    for item in data.get("phrases") or []:
        value = (item.get("value") or "").strip()
        if value:
            phrases.append({"ts": item.get("ts"), "text": value})
    return phrases


@click.group()
def main():
    """Unofficial read-only CLI for your Loom library."""


@main.command("login")
def login_cmd():
    """Open Chrome so you can sign in. Session stays on this machine."""
    asyncio.run(login())


@main.command("list")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
@click.option("--limit", type=int, default=None, help="Stop after this many videos")
@click.option("--id-only", is_flag=True, help="Print only video ids")
def list_cmd(fmt, limit, id_only):
    """List videos in your library."""
    need_session()
    videos = asyncio.run(list_videos(limit))
    if id_only:
        for video in videos:
            click.echo(video["id"])
        return
    if fmt == "json":
        click.echo(json.dumps(videos, indent=2))
        return
    table = Table(title="Looms")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Visibility", style="green")
    for video in videos:
        table.add_row(video["id"], video["title"], video["visibility"])
    console.print(table)
    console.print(f"{len(videos)} videos")


@main.command()
@click.argument("video")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def info(video, fmt):
    """Show one video."""
    need_session()
    vid = video_id(video)
    data = asyncio.run(video_info(vid))
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return
    table = Table(title=data["title"] or vid)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    for key in ("id", "title", "created_at", "duration_seconds", "views", "owner", "url"):
        value = data.get(key)
        if key == "duration_seconds":
            value = fmt_duration(value)
        table.add_row(key, "" if value is None else str(value))
    console.print(table)


@main.command()
@click.argument("video")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.option("-o", "out_path", type=click.Path(), default=None, help="Write the transcript here")
def transcript(video, fmt, out_path):
    """Pull the official transcript."""
    need_session()
    vid = video_id(video)
    phrases = asyncio.run(transcript_phrases(vid))
    if fmt == "json":
        text = json.dumps(phrases, indent=2)
    else:
        lines = []
        for phrase in phrases:
            ts = phrase.get("ts")
            prefix = f"[{fmt_duration(ts)}] " if ts is not None else ""
            lines.append(prefix + phrase["text"])
        text = "\n".join(lines)
    if out_path:
        Path(out_path).write_text(text + ("\n" if text and not text.endswith("\n") else ""))
        console.print(f"Wrote {out_path}")
        return
    click.echo(text)


if __name__ == "__main__":
    main()
