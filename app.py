import re, time, html as h
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageOps
from exporter import make_zip

st.set_page_config(page_title="SketchForge", page_icon="✏️", layout="wide")
st.markdown(f"<style>{(Path(__file__).parent / 'styles.css').read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True)

try:
    import gemma
    from gemma import MODEL
except (KeyError, ValueError):
    st.error("GEMINI_API_KEY is missing. Copy .env.example to .env, add your key, then restart the app.")
    st.stop()

CSP = ("<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
       "style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:\">")

SUGGESTIONS = ["Make the header dark blue", "Add a contact form", "Use a warmer color palette"]
EXAMPLES = Path(__file__).parent / "examples"
HINT = "Try again, or use Load demo."
MAX_SKETCH_SIDE = 2048  # a 12 MP photo sent as PNG is ~24 MB, over the API's inline limit

def valid(page):
    return bool(page) and "<html" in page.lower() and "</html>" in page.lower() and len(page) < 500_000

def explain(fallback):
    """Turn the last API error into one readable line."""
    err = (gemma.last_error or "").lower()
    if not err:
        return fallback
    if any(k in err for k in ("api key", "api_key", "401", "403", "permission_denied", "unauthenticated")):
        return "The API key was rejected. Check GEMINI_API_KEY in your .env file."
    if any(k in err for k in ("429", "quota", "resource_exhausted", "rate limit")):
        return "API quota reached. Wait a minute and try again."
    if any(k in err for k in ("timeout", "timed out", "deadline", "504")):
        return "The model took too long to respond. Try again."
    if any(k in err for k in ("500", "internal", "503", "unavailable", "overloaded")):
        return "The model is busy right now. Try again in a moment."
    return "The request to the Gemini API failed. Check your connection and try again."

def label(num, text):
    st.markdown(f'<div class="sf-tab"><b>{num}</b>{text}</div>', unsafe_allow_html=True)

def render_preview(page, mobile=False, height=680):
    if re.search(r"<head[^>]*>", page, re.I):
        doc = re.sub(r"<head[^>]*>", lambda m: m.group(0) + CSP, page, count=1, flags=re.I)
    else:
        doc = CSP + page
    width = "390px" if mobile else "100%"
    components.html(
        '<style>html,body{margin:0;background:transparent}</style>'
        f'<iframe sandbox="allow-scripts" srcdoc="{h.escape(doc, quote=True)}" '
        f'style="width:{width};height:{height}px;border:1px solid #1A1A1A;border-radius:0;'
        f'background:white;display:block;margin:auto;box-sizing:border-box"></iframe>',
        height=height + 4,
    )

def load_sketch(file):
    """Decode an uploaded PNG/JPG/JPEG into an upright RGB image. Returns (image, error message)."""
    try:
        with Image.open(file) as im:
            if im.format not in ("PNG", "JPEG"):  # checks the content, not just the file extension
                return None, f"That file is not a PNG or JPEG image ({im.format or 'unknown format'}). Upload a PNG, JPG or JPEG."
            im.load()  # forces a full decode so truncated files fail here
            im = ImageOps.exif_transpose(im)  # phone photos store rotation in EXIF
            if im.mode.startswith("I"):  # 16-bit PNG: scale down to 8-bit grey
                im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
            if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")  # transparent areas become white paper, not black
                im = Image.new("RGB", rgba.size, "white")
                im.paste(rgba, mask=rgba.getchannel("A"))
            else:
                im = im.convert("RGB")
            im.thumbnail((MAX_SKETCH_SIDE, MAX_SKETCH_SIDE))  # keeps the API request under its size limit
            return im, None
    except Image.DecompressionBombError:
        return None, "That image is too large to process. Upload a smaller photo."
    except Exception:
        return None, "That file couldn't be read. It may be damaged or not a real PNG, JPG or JPEG image."

@st.cache_resource
def demo_sketch():
    return Image.open(EXAMPLES / "demo_sketch.png").convert("RGB")

class Progress:
    """Step list with live counters, plus the raw output in a collapsed code block.
    Partial HTML is only ever shown as text, never rendered as a page."""
    def __init__(self, steps):
        self.steps = steps
        self.state = dict.fromkeys(steps, "wait")
        self.note = dict.fromkeys(steps, "")
        self.t0 = {}
        self.box = st.empty()
        with st.expander("Live output", expanded=False):
            self.code = st.empty()
            self.code.caption("The page source appears here while it is written.")

    def secs(self, step):
        return f"{time.monotonic() - self.t0.get(step, time.monotonic()):.0f}s"

    def start(self, step, note=""):
        self.state[step], self.note[step], self.t0[step] = "run", note, time.monotonic()
        self.draw()

    def done(self, step, note=None):
        self.state[step] = "done"
        if note is not None:
            self.note[step] = note
        self.draw()

    def fail(self):
        for s in self.steps:
            if self.state[s] == "run":
                self.state[s], self.note[s] = "fail", "failed"
        self.draw()

    def draw(self):
        rows = "".join(f'<li class="sf-step sf-step-{self.state[s]}"><span class="sf-step-mark"></span>'
                       f'<span>{s}</span><span class="sf-step-note">{h.escape(self.note[s])}</span></li>'
                       for s in self.steps)
        self.box.markdown(f'<ol class="sf-progress">{rows}</ol>', unsafe_allow_html=True)

    def stream(self, pieces, write_step, plan_step=None, extract=True):
        """Consume a model stream, updating counters about every 0.3 s. Returns the page (or raw text)."""
        def update(thought_chars, text):
            if not text:
                step = plan_step or write_step
                what = f"{thought_chars:,} chars of planning" if thought_chars else "waiting for first output"
                self.note[step] = f"{what} · {self.secs(step)}"
            else:
                if plan_step and self.state[plan_step] == "run":
                    self.done(plan_step, f"{self.secs(plan_step)}" if thought_chars else "skipped")
                if self.state[write_step] != "run":
                    self.start(write_step)
                lines = text.count("\n") + 1
                self.note[write_step] = f"{lines} lines · {len(text):,} chars · {self.secs(write_step)}"
                self.code.code(text[-3000:], language="html" if extract else None)
            self.draw()
        return gemma.collect_stream(pieces, update, extract=extract)

# session state
st.session_state.setdefault("versions", [])
st.session_state.setdefault("chat", [])
st.session_state.setdefault("demo", False)          # preview is the pre-generated demo page
st.session_state.setdefault("demo_sketch", False)   # show the demo sketch until the user uploads one

with st.sidebar:
    st.markdown('<div class="sf-mini">Settings</div>', unsafe_allow_html=True)
    fast = st.checkbox("Fast mode (experimental): faster page generation, less planning", key="fast")
    st.caption("Sketch reading and edits always skip Gemma 4's planning step. "
               "This setting also skips it when writing the page, which is faster but less tested.")
    st.markdown(f'<div class="sf-mini">Model<br>{h.escape(MODEL)}</div>', unsafe_allow_html=True)

st.markdown(
    '<div class="sf-head"><div class="sf-title" role="heading" aria-level="1">SketchForge</div>'
    '<p class="sf-sub">Photograph a website you drew on paper. Get a working, responsive page '
    'you can refine by typing and download as a file.</p></div>',
    unsafe_allow_html=True,
)

left, right = st.columns([1, 1.4], gap="large")

with left:
    with st.container(key="sf-panel-sketch"):
        label("01", "Sketch")
        up = st.file_uploader("Photo of your sketch (PNG, JPG or JPEG)", type=["png", "jpg", "jpeg"])
        img, caption = None, "Your sketch"
        if up:
            img, problem = load_sketch(up)
            if problem:
                st.error(problem)
        elif st.session_state.demo_sketch:
            try:
                img, caption = demo_sketch(), "Demo sketch"
            except OSError:
                st.error("The demo sketch is missing from examples/.")
        if img is not None:
            st.image(img, caption=caption, width="stretch")

        with st.container(key="sf-actions", horizontal=True, gap="small"):
            generate = st.button("Generate website", type="primary", disabled=img is None, width="stretch")
            if st.button("Load demo"):
                try:
                    demo_page = (EXAMPLES / "demo_site.html").read_text(encoding="utf-8")
                except OSError:
                    demo_page = None
                if valid(demo_page):
                    st.session_state.versions = [demo_page]
                    st.session_state.chat = []
                    st.session_state.demo = True
                    st.session_state.demo_sketch = True
                    st.rerun()
                else:
                    st.error("The demo page is missing or invalid in examples/.")

        if generate:
            steps = ["Reading your sketch", "Planning", "Writing the page", "Checking the result", "Done"]
            prog = Progress(steps)
            started = time.monotonic()
            prog.start(steps[0])
            spec = prog.stream(gemma.interpret_sketch_stream(img, fast), steps[0], extract=False)
            if not spec:
                prog.fail()
                st.error(f"{explain('Could not read the sketch. Try a clearer, well-lit photo.')} {HINT}")
            else:
                prog.done(steps[0], prog.secs(steps[0]))
                prog.start(steps[1])
                page = prog.stream(gemma.generate_html_stream(img, spec, fast), steps[2], plan_step=steps[1])
                if prog.state[steps[1]] == "run" and gemma.last_error is None:
                    prog.done(steps[1])
                if page is None and gemma.last_error is None:  # replied, but not with a page: retry once
                    prog.start(steps[2], "retrying")
                    page = prog.stream(gemma.generate_html_stream(img, spec, fast, retry=True), steps[2])
                if prog.state[steps[2]] == "run":
                    prog.done(steps[2])
                prog.start(steps[3])
                if valid(page):
                    prog.done(steps[3], "valid")
                    prog.done(steps[4], f"total {time.monotonic() - started:.0f}s")
                    st.session_state.versions = [page]
                    st.session_state.chat = []
                    st.session_state.demo = False
                else:
                    prog.fail()
                    st.error(f"{explain('The model returned an empty or incomplete page.')} {HINT}")

    with st.container(key="sf-panel-refine"):
        label("03", "Refine")
        if not st.session_state.chat:
            st.markdown('<p class="sf-hint">Describe a change in plain words. Each edit becomes '
                        'a new version you can undo.</p>', unsafe_allow_html=True)
        for role, text in st.session_state.chat:
            who = "You" if role == "user" else "SketchForge"
            body = h.escape(text).replace("\n", "<br>")
            st.markdown(f'<div class="sf-msg sf-msg-{role}"><div class="sf-msg-who">{who}</div>'
                        f'<div class="sf-msg-text">{body}</div></div>', unsafe_allow_html=True)

        instruction = st.chat_input("Describe a change, e.g. make the header dark blue",
                                    disabled=not st.session_state.versions)
        st.markdown('<div class="sf-mini">Try</div>', unsafe_allow_html=True)
        with st.container(key="sf-suggest", horizontal=True, gap="small"):
            for i, s in enumerate(SUGGESTIONS):
                if st.button(s, key=f"sf-suggest-{i}", disabled=not st.session_state.versions):
                    instruction = s
        if instruction:
            st.session_state.chat.append(("user", instruction))
            steps = ["Writing the changes", "Checking the result", "Done"]
            prog = Progress(steps)
            prog.start(steps[0])
            updated = prog.stream(gemma.edit_html_stream(st.session_state.versions[-1], instruction), steps[0])
            if valid(updated):
                st.session_state.versions.append(updated)
                st.session_state.chat.append(("assistant", "Done. Check the preview."))
            else:
                reason = explain("The model returned an empty or incomplete page.")
                st.session_state.chat.append(("assistant", f"{reason} Your last working version is kept."))
            st.rerun()

with right:
    with st.container(key="sf-panel-preview"):
        label("02", "Preview")
        if st.session_state.versions:
            with st.container(key="sf-toolbar", horizontal=True, vertical_alignment="center", gap="small"):
                mobile = st.toggle("Mobile 390px")
                st.markdown(f'<div class="sf-mini sf-version">Version {len(st.session_state.versions)}</div>',
                            unsafe_allow_html=True)
                if st.button("Undo", disabled=len(st.session_state.versions) < 2):
                    st.session_state.versions.pop()
                    st.rerun()
                st.download_button("Download ZIP", make_zip(st.session_state.versions[-1]),
                                   "sketchforge-site.zip", "application/zip")
            if st.session_state.demo and len(st.session_state.versions) == 1:
                st.markdown('<div class="sf-demo-note">Demo result (pre-generated). '
                            'Upload your own sketch to generate live.</div>', unsafe_allow_html=True)
            with st.container(key="sf-sheet"):
                render_preview(st.session_state.versions[-1], mobile)
        else:
            st.markdown(
                '<div class="sf-empty"><p class="sf-empty-lead">Draw a page on paper. '
                'SketchForge builds it here.</p><ol class="sf-steps">'
                '<li><span>01</span>Upload sketch</li><li><span>02</span>Generate</li>'
                '<li><span>03</span>Refine by chat</li></ol></div>',
                unsafe_allow_html=True,
            )

st.markdown(f'<div class="sf-foot">Gemma 4 via Gemini API <span>/</span> {h.escape(MODEL)}</div>',
            unsafe_allow_html=True)
