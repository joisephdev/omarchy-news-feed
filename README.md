# News Feed for Omarchy

Read headlines from any RSS feed — Yahoo Finance by default — without leaving
the Omarchy bar.

## Highlights

- Fetches headlines from any RSS 2.0 feed URL you configure.
- Ships with a working default: Yahoo Finance top stories
  (`finance.yahoo.com/news/rssindex`). Yahoo retired its general-purpose news
  RSS feed, so this is the Yahoo feed that is actually online today.
- Filter the fetched headlines by title or source as you type.
- Open a selected headline in your default browser.
- Auto-refreshes on a configurable interval (or manually, any time).
- Navigate entirely by keyboard or use the mouse.
- Match the active Omarchy theme, typography, and panel styling.
- Read-only: nothing is written to disk, no database, no telemetry.

## Install

```sh
omarchy plugin add https://github.com/joisephdev/omarchy-news-feed --enable
```

The `News` widget is placed in the center section by default. Move it
anywhere in the bar with:

```sh
omarchy bar move synapsync.news-feed --section right
```

## Configure the feed

1. Open `News` from the bar.
2. Select the settings icon.
3. Set the **Feed URL**, the **Feed name** shown in the placeholder text, how
   many **headlines** to fetch (1-50), and the **refresh interval** in
   minutes (0 disables auto-refresh).
4. Select **Save**.

Settings are stored in `~/.config/omarchy/shell.json` and survive shell and
system restarts. You can also configure them from a terminal:

```sh
omarchy bar set synapsync.news-feed feedUrl "https://finance.yahoo.com/news/rssindex"
omarchy bar set synapsync.news-feed feedName "Yahoo Finance"
omarchy bar set synapsync.news-feed itemLimit 25
omarchy bar set synapsync.news-feed refreshMinutes 15
```

### Other feeds

Any RSS 2.0 URL works, for example:

- Yahoo Finance top stories (default): `https://finance.yahoo.com/news/rssindex`
- Yahoo Finance for a specific ticker: `https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US`
- Google News search (useful as a stand-in for Yahoo's discontinued general
  news feed): `https://news.google.com/rss/search?q=site:yahoo.com&hl=en-US&gl=US&ceid=US:en`
- Any other outlet's public RSS feed (BBC, NYT, etc.)

## Read headlines

Open the panel to see the latest fetch. Typing filters the already-fetched
list by title or source — it does not requery the feed.

- **Up / Down** — move through headlines
- **Enter** — open the selected headline in your browser
- **Escape** — clear the filter; press again to close the panel
- **Tab** — move to the next bar panel
- **Left click** — open or close the News panel
- **Right click** — refresh without opening the panel

## Privacy and network access

- `fetch-news.py` makes a single HTTP(S) GET request to the configured feed
  URL and reads nothing else.
- No filter text, click history, or settings are sent anywhere; only the feed
  URL you configured is contacted.
- No index, cache, telemetry, or background daemon is created — headlines
  live in memory only for as long as the panel process runs.

Like all Omarchy shell plugins, this plugin runs with your user permissions.
Review the source before installing third-party plugins, and only point it at
feed URLs you trust.

## Requirements

- Omarchy Quattro with the Quickshell-based shell
- Python 3 (standard library only — no `pip install` needed)
- A default browser or URL handler for `xdg-open`

## Update

```sh
omarchy plugin update synapsync.news-feed
```

## Remove

```sh
omarchy plugin remove synapsync.news-feed
```

## Troubleshooting

### The panel is empty / "Fetch failed"

Confirm `python3` is on your `PATH` and that the feed URL is reachable:

```sh
python3 ~/.config/omarchy/plugins/synapsync.news-feed/fetch-news.py "https://finance.yahoo.com/news/rssindex" 5
```

This should print a JSON array. An empty `[]` means the feed URL didn't
return valid RSS — check it in a browser, or check network connectivity.

### Headlines show no timestamp

Some feeds omit `pubDate` per item; the widget then just shows the source
name. This does not affect fetching or opening headlines.

### Changes do not appear after development edits

User plugins normally hot-reload. Force discovery when needed:

```sh
omarchy-shell shell rescanPlugins
```

For a structural QML change (new elements, reordered layout — not just text
or logic tweaks), `rescanPlugins` can leave a stale compiled version on
screen even though it logs a reload. If the panel still doesn't match the
source after a rescan, fully restart the shell instead:

```sh
omarchy restart shell
```

## Development

Validate the plugin directory before publishing:

```sh
omarchy plugin validate .
python3 -m py_compile fetch-news.py
```

The plugin has no build step and no downloaded runtime dependencies beyond a
standard Python 3 install.

## License

[MIT](LICENSE)
