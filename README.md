# Novel Updates Filter

A Chrome extension for [Novel Updates](https://www.novelupdates.com). Unofficial, not affiliated with Novel Updates. Built with the help of Claude Code.


## What it does

- **Hide what you've read.** Mark a novel Read, Not Finished or Not Interested and it's gone from every listing. Rate it while you're there.
- **Score the rest.** Every other novel gets a 1–10 score based on what you've read and rated (tags, genres and the synopsis). Click a score to see why.
- **Filter.** Hide languages, genres, low Novel Updates ratings or low scores.
- **To Read list.** A sidebar on every page, with covers, synopses and a Start reading button that goes straight to chapter 1.
- **Endless scroll.** The next page loads in place.
- **Manage everything.** A full-page manager to search, sort, move, export and import your lists.

Support me: [buy me a coffee on Ko-fi](https://ko-fi.com/gaossian0496).

## Install

From the Chrome Web Store (coming soon), or load it yourself:

1. Download or clone this repository.
2. Open `chrome://extensions` and turn on **Developer mode**.
3. Click **Load unpacked** and pick the `application/` folder.
4. Refresh any Novel Updates tabs you already had open.

## Development

There's no build step: `application/` is the extension as it ships.

Tests are Playwright and pytest, run against local copies of the site's pages:

```
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest tests/
```

The suite opens a real browser window and takes several minutes.

## License

MIT, see `LICENSE`.
