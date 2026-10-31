from PIL import Image
from gemma import interpret_sketch, generate_html, MODEL

print("Using model:", MODEL)
img = Image.open("sketch.png")
spec = interpret_sketch(img)
print("SPEC:\n", spec)
page = generate_html(img, spec)
open("test.html", "w", encoding="utf-8").write(page or "FAILED")
print("Saved test.html" if page else "Generation failed")
