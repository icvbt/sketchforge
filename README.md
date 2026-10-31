# SketchForge

Draw a website on paper, take a photo, and get a working web page back. Then change it by typing what you want.

## The problem

Lots of people can sketch the website they want: a club page, a menu for a small shop, a portfolio. Far fewer can turn that sketch into HTML and CSS. Website builders push everyone into the same templates, and hiring a developer for a one-page site is slow and costs money.

## Who it's for

Anyone who can sketch a website, especially college clubs, small businesses and creators who need a simple page quickly.

## How it works

1. **Upload a sketch.** A photo of a hand-drawn page (PNG or JPG).
2. **Gemma 4 reads it.** The model turns the photo into a short layout spec: what kind of site it is, each section from top to bottom, every readable label, and the buttons, links and forms it implies.
3. **Gemma 4 writes the page.** From the image and the spec, it writes one self-contained, responsive HTML page with inline CSS and JavaScript and no external files.
4. **Edit by chat.** Type a change ("make the header dark blue") or click a suggestion. The model gets the full current page plus your instruction and returns the full updated page.
5. **Undo** steps back through every version.
6. **Download** a ZIP containing `index.html`.

SketchForge runs on **Gemma 4**, Google's open-weight model, through the Gemini API.

## Features

- **Load demo:** shows a pre-generated result from the example sketch in `examples/` instantly, with no API call. Use it for a quick look. Editing the demo by chat still uses the real model.
- **Live progress:** while the model works, a step list shows what is happening (reading the sketch, planning, writing the page, checking the result) with live counts of characters, lines and seconds. The raw page source streams into a collapsed "Live output" box. The preview only updates once the page is complete and has passed validation.
- Live preview with a 390px mobile view.
- Plain one-line error messages (missing key, quota, timeout, server busy), and the last working version is always kept.

## Thinking levels

Gemma 4 plans (thinks) before it writes, and that planning is most of the waiting time. The Gemini API accepts two thinking levels for Gemma 4: the default, and `MINIMAL`, which skips planning.

| Step | Thinking level | Why |
|---|---|---|
| Reading the sketch | `MINIMAL` | About 12 s instead of about 75 s, with an equally accurate spec in our test. |
| Writing the page | Default | In our test the page was more faithful to the sketch with planning. |
| Edits | `MINIMAL` | Edits are small, targeted changes. |

**Fast mode (experimental)** in the sidebar uses `MINIMAL` for writing the page too. In our test it was about 40% faster but less faithful to the sketch (it renamed a menu item and left out content). It is off by default.

If the API rejects a thinking setting, SketchForge falls back to the default and carries on. The levels are set at the top of `gemma.py`.

## Setup

Requires Python 3.10+ and a Gemini API key from [Google AI Studio](https://aistudio.google.com/).

**Windows (PowerShell)**

```powershell
git clone https://github.com/icvbt/sketchforge.git
cd sketchforge
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env                 # set GEMINI_API_KEY=your_key_here
streamlit run app.py
```

If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**macOS / Linux**

```bash
git clone https://github.com/icvbt/sketchforge.git
cd sketchforge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env                    # set GEMINI_API_KEY=your_key_here
streamlit run app.py
```

The app opens at http://localhost:8501.

## Switching models

The model is read from the `GEMMA_MODEL` environment variable. You can put it in `.env`:

| Model | Notes |
|---|---|
| `gemma-4-26b-a4b-it` | Default. Mixture-of-experts, faster. |
| `gemma-4-31b-it` | Larger dense model. Slower. |

```
GEMMA_MODEL=gemma-4-31b-it
```

## Safety

- The preview runs in a sandboxed iframe with `allow-scripts` only and **no** `allow-same-origin`, so generated code can't reach the app, its cookies or its storage.
- A Content Security Policy is injected into every preview (`default-src 'none'`), which blocks network requests, external scripts, fonts and remote images.
- Generated code is never run on the server. It is only shown in the browser preview and written into the ZIP.
- Partial output is never rendered. A page is shown only after it is complete and passes validation.
- Requests time out after 6 minutes, so a stuck generation can't hang the app.
- The API key is loaded from `.env`, which is git-ignored.

## Limitations

- **Generation takes about 2 to 4 minutes** with the default settings. Use Load demo for a quick look.
- Messy, dense or low-contrast sketches can be misread. A clear photo of dark pen on white paper works best.
- The X that marks an image area in a sketch sometimes still shows up in the page, as an X or faint diagonal lines.
- Output varies between runs. If a result isn't right, generate again or fix it by chat.
- External images, fonts and scripts are blocked in the preview, so pages use system fonts, inline SVG and CSS patterns instead.
- Very large pages (over 500,000 characters) are rejected.

## Screenshots

_Screenshots coming soon._

## License

[MIT](LICENSE)
