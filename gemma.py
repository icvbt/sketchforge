import os, re, time, itertools, datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
MODEL = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it")
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MAX_OUTPUT_TOKENS = 32768  # Gemma 4 output limit reported by the API; long pages get cut off otherwise
TIMEOUT_MS = 360_000  # 6 minutes; the API rejects deadlines under 10 s

# Thinking levels. Gemma 4 accepts only "MINIMAL" (no planning step) and "HIGH" (same as default);
# "LOW"/"MEDIUM" and thinking_budget are rejected. None means the model's default.
FAST_THINKING = "MINIMAL"
SPEC_THINKING = FAST_THINKING  # reading the sketch: planning adds ~60 s without a better spec
EDIT_THINKING = FAST_THINKING
def generation_thinking(fast):
    return FAST_THINKING if fast else None

SPEC_PROMPT = """Read this hand-drawn website sketch. Write a compact spec in exactly this format:

SITE: what kind of site this is and who it is for (one line)
STYLE HINTS: colors or mood the author explicitly wrote or colored in, or "none" (the pencil drawing style itself is not a hint)
SECTIONS (top to bottom):
1. <section name> | layout: columns, what sits left/right, relative height (small/medium/large)
   text: every readable label, quoted exactly as written
   images: where image areas (boxes with an X) are and their rough size
   interactive: buttons, links, nav items, forms, tabs, and what each should do
2. ...

Write [unreadable] for text you cannot read. Only list sections that are drawn. No HTML."""

OUTPUT_RULES = """OUTPUT
- ONE complete HTML document from <!DOCTYPE html> to </html>, with a viewport meta tag, one <style> and one <script>.
- No external resources at all: no <link>, web fonts, CDNs, image URLs or fetch. They are blocked.
- Keep it compact: concise CSS, no repeated blocks, about 400 lines at most.
- Output only the HTML. No explanation, no markdown fences."""

GENERATE_PROMPT = """Build a real, working website from this hand-drawn sketch (image) and its spec.

SPEC:
{spec}

1. FIDELITY (most important)
- Keep the sketch's sections, order, proportions and left/right placement. Use every readable label exactly as written. Do not add major sections. Do not drop any.
- Where text is unreadable or missing, write specific, believable copy for this site's real purpose and audience. Never use filler such as "Lorem ipsum", "Welcome to our platform", "Your trusted partner", "Unlock the power of", "Seamless" or "Elevate".

2. A USEFUL, WORKING SITE
- Nav links smooth-scroll to section ids. A working mobile menu button. Every form validates and shows a visible success message. Hover and focus states. Tabs or accordions where the sketch implies them. Small inline vanilla JS only.
- Fill the drawn sections with the real content this kind of site needs: a club gets events with dates and a join form, a shop gets products with prices and an order button, a portfolio gets projects with descriptions.
- The current year is {year}. Any dates, event listings and the footer copyright use {year}, and event dates are in the future.
- Accessible: header, nav, main, section, footer; exactly one h1; buttons are buttons and links are links; aria-label on SVGs; visible focus outlines; strong contrast; respect prefers-reduced-motion.
- Mobile-first and responsive. Check it works at 390px and 1280px wide.

3. DESIGN (it must look made by a skilled human designer, not an AI template)
- Choose ONE visual direction that fits the content (for example editorial, brutalist, warm handmade, technical monospace, soft organic, bold poster) and commit to it.
- Palette: 3 or 4 colors as CSS variables (--bg, --ink, --accent, --muted). --accent is one strong, saturated color that suits the subject, never grey. No purple, indigo or blue-to-purple. No gradient backgrounds or gradient text unless the sketch asks for them.
- Type: system font stacks only, e.g. Georgia, "Iowan Old Style", "Palatino Linotype", serif for headings with system-ui, "Segoe UI", sans-serif or ui-monospace, monospace for body. Large size contrast, tight letter-spacing on big headings, line-height around 1.6, paragraphs no wider than 65ch.
- Layout: asymmetry, varied section rhythm, generous whitespace on one spacing scale. Do not make every section a centered three-card grid.
- Visuals: no emoji or Unicode symbols as icons. A box with an X in the sketch means "image here": never draw the X, fill it with an illustrative composition of inline SVG shapes or CSS patterns in the palette. Icons (including the menu button) are simple inline SVG with one consistent stroke width.
- Craft: one border-radius system, thin borders instead of heavy shadows, CSS transitions only, a proper footer.
- Avoid: glassmorphism, glowing orbs, blurred blobs, neon on dark, rainbow gradients, emoji feature cards, fake testimonials, made-up stats like "10k+ users".
Use 2026 for any dates and the footer year
{output}"""

EDIT_PROMPT = """Current website:
{html}

Apply ONLY this change: {instruction}

- Leave everything else exactly as it is unless the change requires touching it.
- Keep the existing visual direction, CSS variables, fonts and JS behavior.
- For style changes (e.g. "make it warmer"), update the CSS variables and related rules consistently, not just one element.
- Keep the design quality: no emoji icons, no purple gradients, unless the change asks for them.
- Still no external resources (links, web fonts, CDNs, image URLs, fetch).

Return the full updated document from <!DOCTYPE html> to </html>. Output only the HTML, no explanation, no markdown fences."""

RETRY_REMINDER = ("\n\nYour previous reply was not a complete HTML document. Reply with ONLY the complete "
                  "HTML document, starting with <!DOCTYPE html> and ending with </html>. Keep it compact.")

def extract_html(text):
    text = re.sub(r"```(?:html)?", "", text or "", flags=re.I)  # drop markdown fences
    start = re.search(r"<!DOCTYPE html|<html[\s>]", text, re.I)
    if not start:
        return None
    doc = text[start.start():]
    end = re.search(r"</html\s*>", doc, re.I)
    if end:
        doc = doc[:end.end()]
    elif re.search(r"</body\s*>", doc, re.I):  # only </html> missing: easy to recover
        doc = doc[:re.search(r"</body\s*>", doc, re.I).end()] + "\n</html>"
    else:  # cut off mid-page: not recoverable
        return None
    if not doc.lower().startswith("<!doctype"):
        doc = "<!DOCTYPE html>\n" + doc
    return doc

last_error = None  # text of the most recent API failure, read by the UI

def _config(max_tokens=None, thinking=None):
    kw = {"http_options": types.HttpOptions(timeout=TIMEOUT_MS)}
    if max_tokens:
        kw["max_output_tokens"] = max_tokens
    if thinking:
        kw["thinking_config"] = types.ThinkingConfig(thinking_level=thinking)
    return types.GenerateContentConfig(**kw)

def _thinking_rejected(e, thinking):
    return bool(thinking) and "thinking" in str(e).lower()

def _ask(contents, max_tokens=None, thinking=None):
    global last_error
    last_error = None
    try:
        try:
            return client.models.generate_content(model=MODEL, contents=contents,
                                                  config=_config(max_tokens, thinking)).text
        except Exception as e:
            if not _thinking_rejected(e, thinking):
                raise
            print("Thinking setting rejected, using default:", e)
            return client.models.generate_content(model=MODEL, contents=contents,
                                                  config=_config(max_tokens)).text
    except Exception as e:
        last_error = str(e)
        print("API error:", e)
        return None

def _stream(contents, max_tokens=None, thinking=None):
    """Yield ("thought", text) and ("text", text) pieces as they arrive. Sets last_error on failure."""
    global last_error
    last_error = None
    started = time.monotonic()
    def open_stream(level):
        chunks = iter(client.models.generate_content_stream(model=MODEL, contents=contents,
                                                            config=_config(max_tokens, level)))
        first = next(chunks, None)  # the request is sent here, so a rejected setting fails here
        return itertools.chain([first] if first else [], chunks)
    try:
        try:
            chunks = open_stream(thinking)
        except Exception as e:
            if not _thinking_rejected(e, thinking):
                raise
            print("Thinking setting rejected, using default:", e)
            chunks = open_stream(None)
        for chunk in chunks:
            if time.monotonic() - started > TIMEOUT_MS / 1000:
                raise TimeoutError("generation timed out")
            cand = chunk.candidates[0] if chunk.candidates else None
            for part in (cand.content.parts or []) if cand and cand.content else []:
                if part.text:
                    yield ("thought" if part.thought else "text", part.text)
    except Exception as e:
        last_error = str(e)
        print("API error:", e)

def collect_stream(pieces, on_update=None, every=0.3, extract=True):
    """Assemble a stream into a page (or raw text if extract=False). Thought parts are only counted,
    never added to the output. on_update(thought_chars, text_so_far) runs at most every `every` s and at the end."""
    text, thought_chars, last = [], 0, 0.0
    for kind, piece in pieces:
        if kind == "thought":
            thought_chars += len(piece)
        else:
            text.append(piece)
        if on_update and time.monotonic() - last >= every:
            last = time.monotonic()
            on_update(thought_chars, "".join(text))
    if on_update:
        on_update(thought_chars, "".join(text))
    return extract_html("".join(text)) if extract else ("".join(text).strip() or None)

def interpret_sketch(image, fast=False):  # `fast` kept for compatibility; the spec always uses SPEC_THINKING
    return _ask([image, SPEC_PROMPT], thinking=SPEC_THINKING)

def interpret_sketch_stream(image, fast=False):
    return _stream([image, SPEC_PROMPT], thinking=SPEC_THINKING)

def _generate_prompt(spec):
    return GENERATE_PROMPT.format(spec=spec, output=OUTPUT_RULES, year=datetime.date.today().year)

def generate_html(image, spec, fast=False):
    prompt = _generate_prompt(spec)
    level = generation_thinking(fast)
    page = extract_html(_ask([image, prompt], MAX_OUTPUT_TOKENS, level))
    if page is None and last_error is None:  # model replied but not with a usable page: retry once
        page = extract_html(_ask([image, prompt + RETRY_REMINDER], MAX_OUTPUT_TOKENS, level))
    return page

def generate_html_stream(image, spec, fast=False, retry=False):
    prompt = _generate_prompt(spec) + (RETRY_REMINDER if retry else "")
    return _stream([image, prompt], MAX_OUTPUT_TOKENS, generation_thinking(fast))

def edit_html(current_html, instruction):
    prompt = EDIT_PROMPT.format(html=current_html, instruction=instruction)
    return extract_html(_ask([prompt], MAX_OUTPUT_TOKENS, EDIT_THINKING))

def edit_html_stream(current_html, instruction):
    prompt = EDIT_PROMPT.format(html=current_html, instruction=instruction)
    return _stream([prompt], MAX_OUTPUT_TOKENS, EDIT_THINKING)
