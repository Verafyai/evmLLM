"""Draw the judge handoff diagram and the withdrawal prompt card.

Run from the repo root: PYTHONPATH=. python assets/render_judge_graphics.py
The prompt text is the real build_prompt output for the withdrawal scenario.
The answer is SimulatedLLM. No model is called.
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from evmllm.judge import EXAMPLE_WITHDRAW, SimulatedLLM, build_prompt, vm_record

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
INK = (22, 32, 46)
MUTE = (90, 101, 118)
BLUE = (26, 95, 208)
BLUE2 = (13, 63, 153)
SKY = (233, 241, 252)
SKY2 = (180, 205, 240)
WHITE = (255, 255, 255)
LINE = (212, 220, 230)
OK = (29, 138, 74)
OK_BG = (227, 244, 234)
WARN = (183, 121, 31)
WARN_BG = (255, 248, 238)
BAD = (192, 57, 43)
BAD_BG = (248, 225, 222)
NAVY = (15, 29, 51)
PALE = (250, 251, 253)

SANS = "/usr/share/fonts/truetype/macos/Inter-Regular.ttf"
SANS_MED = "/usr/share/fonts/truetype/macos/Inter-Medium.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/macos/Inter-Bold.ttf"
MONO = "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Bold.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


def wrap(text, face, max_w):
    if text == "":
        return [""]
    words = text.split(" ")
    lines, cur = [], ""
    for word in words:
        trial = word if not cur else cur + " " + word
        if face.getlength(trial) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def center_text(draw, box, lines, face, fill, gap=4):
    x0, y0, x1, y1 = box
    heights = []
    for line in lines:
        b = face.getbbox(line or " ")
        heights.append(b[3] - b[1])
    total = sum(heights) + gap * (len(lines) - 1)
    y = y0 + (y1 - y0 - total) / 2
    for line, h in zip(lines, heights):
        w = face.getlength(line)
        draw.text((x0 + (x1 - x0 - w) / 2, y), line, font=face, fill=fill)
        y += h + gap


def pill(draw, cx, cy, text, face, fg, bg, pad_x=14, pad_y=7):
    tw = face.getlength(text)
    b = face.getbbox(text)
    th = b[3] - b[1]
    w, h = tw + pad_x * 2, th + pad_y * 2
    x, y = cx - w / 2, cy - h / 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=bg)
    draw.text((x + pad_x, y + pad_y - b[1]), text, font=face, fill=fg)
    return w, h


def arrow(draw, x1, y, x2, color, width=5):
    draw.line([(x1, y), (x2 - 16, y)], fill=color, width=width)
    draw.polygon([(x2, y), (x2 - 18, y - 9), (x2 - 18, y + 9)], fill=color)


def load_mark():
    im = Image.open(OUT / "evmllm-mark.png").convert("RGBA")
    # Keep it only when the corners are transparent, so a flat background is not pasted in.
    px = im.getpixel((0, 0))
    if len(px) < 4 or px[3] > 20:
        return None
    return im


def draw_handoff(path):
    W, H = 1600, 680
    im = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(im)
    title_f = font(SANS_BOLD, 54)
    sub_f = font(SANS, 26)
    head_f = font(SANS_BOLD, 26)
    body_f = font(SANS, 20)
    small_f = font(SANS_BOLD, 16)
    foot_f = font(SANS, 22)
    d.text((56, 40), "How a transaction is handed to the judge", font=title_f, fill=INK)
    d.text((56, 112), "The contract runs exactly. Then the result is handed over.", font=sub_f, fill=MUTE)
    mark = load_mark()
    if mark:
        mark.thumbnail((72, 72))
        im.paste(mark, (W - 56 - mark.width, 36), mark)

    labels = [
        ("Transaction", "A wallet asks\nto call a contract", WHITE, LINE, INK),
        ("VM circuits", "Exact smart-contract\nresult", SKY, BLUE, BLUE2),
        ("Prompt wrapper", "The result, the history,\nand the rules", WHITE, LINE, INK),
        ("LLM judge", "Reads the prompt\nand answers", WARN_BG, (224, 196, 138), INK),
        ("Decision", "", WHITE, LINE, INK),
    ]
    top, box_h = 210, 280
    margin = 40
    gaps = [28, 188, 28, 28]  # the wide gap is the handoff, between VM circuits and the prompt
    n = len(labels)
    box_w = (W - margin * 2 - sum(gaps)) // n
    boxes = []
    x = margin
    for i, lab in enumerate(labels):
        boxes.append((x, top, x + box_w, top + box_h))
        if i < n - 1:
            x += box_w + gaps[i]
    for (x0, y0, x1, y1), (title, body, fill, outline, color) in zip(boxes, labels):
        d.rounded_rectangle([x0, y0, x1, y1], radius=22, fill=fill, outline=outline, width=3)
        d.text((x0 + 18, y0 + 22), title, font=head_f, fill=color)
        if body:
            yy = y0 + 78
            for line in body.split("\n"):
                d.text((x0 + 18, yy), line, font=body_f, fill=MUTE)
                yy += 30
    # SIMULATED tag on the LLM box.
    lx0, ly0, lx1, ly1 = boxes[3]
    pill(d, (lx0 + lx1) / 2, ly0 + 175, "SIMULATED", small_f, WHITE, WARN, 16, 8)
    # Decision pills.
    dx = (boxes[4][0] + boxes[4][2]) / 2
    pill(d, dx, boxes[4][1] + 110, "allow", small_f, OK, OK_BG, 18, 8)
    pill(d, dx, boxes[4][1] + 168, "hold", small_f, WARN, (251, 240, 220), 18, 8)
    pill(d, dx, boxes[4][1] + 226, "block", small_f, WHITE, BAD, 18, 8)

    # Arrows sit in the gaps. The one after the VM box is the handoff.
    for i in range(n - 1):
        x1 = boxes[i][2] + 4
        x2 = boxes[i + 1][0] - 4
        mid_y = top + box_h / 2 + 10
        if i == 1:
            arrow(d, x1, mid_y + 16, x2, BLUE, 6)
            pill(d, (x1 + x2) / 2, mid_y - 28, "handed over here", small_f, BLUE2, SKY, 10, 6)
        else:
            arrow(d, x1, mid_y, x2, (167, 173, 182), 4)

    d.text((56, H - 78), "The reviewer step is simulated. No real language model was called.", font=foot_f, fill=MUTE)
    d.text((56, H - 44), "The answer can only get stricter than the rule check.", font=foot_f, fill=MUTE)
    im.save(path, "PNG")
    print("wrote", path, im.size)


def draw_prompt_card(path):
    cases = json.loads((ROOT / "results" / "judge.json").read_text())
    s = next(c for c in cases if c["id"] == EXAMPLE_WITHDRAW)
    vm = vm_record(s["vm"]["status"], s["vm"]["gas_used"], s["vm"]["outcome"], logs=[])
    prompt = build_prompt(s["tx"], vm, s["ctx"], s["verdict"]).rstrip("\n")
    sim = SimulatedLLM(EXAMPLE_WITHDRAW)
    answer = sim.labeled(prompt + "\n")

    marker = "VM result (exact)"
    before, rest = prompt.split(marker, 1)
    vm_and_after = marker + rest
    end = vm_and_after.index("\n\nSender and receiver")
    vm_text = vm_and_after[:end]
    after = vm_and_after[end + 2:]

    W = 1600
    margin = 56
    inner = 36
    text_w = W - margin * 2 - inner * 2
    title_f = font(SANS_BOLD, 48)
    sub_f = font(SANS, 24)
    sec_f = font(SANS_BOLD, 22)
    mono_f = font(MONO, 16)
    mono_b = font(MONO_BOLD, 16)
    foot_f = font(SANS, 22)
    line_h = 24

    def block_lines(text, face):
        out = []
        for raw in text.split("\n"):
            out.extend(wrap(raw, face, text_w))
        return out

    before_lines = block_lines(before.rstrip("\n"), mono_f)
    vm_lines = block_lines(vm_text, mono_f)
    after_lines = block_lines(after, mono_f)
    answer_lines = block_lines(answer, mono_b)

    def block_h(lines):
        return inner * 2 + line_h * len(lines)

    y_title = 40
    y = 150
    gap = 18
    h_before = block_h(before_lines)
    h_vm = block_h(vm_lines)
    h_after = block_h(after_lines)
    h_answer = block_h(answer_lines)
    caption_h = 42
    H = y + h_before + caption_h + h_vm + gap + h_after + gap + h_answer + 120
    im = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(im)
    d.text((margin, y_title), "Withdrawal: the prompt and a simulated answer", font=title_f, fill=INK)
    d.text((margin, y_title + 62), "The prompt wraps the exact VM result. No real language model was called.", font=sub_f, fill=MUTE)

    def panel(y0, h, lines, fill, fg, outline, face):
        d.rounded_rectangle([margin, y0, W - margin, y0 + h], radius=18, fill=fill, outline=outline, width=3)
        yy = y0 + inner
        for line in lines:
            # Section labels inside the prompt stay a bit stronger.
            use = face
            color = fg
            if line in ("Role", "Transaction request", "VM result (exact)", "Sender and receiver",
                        "Sender history", "Receiver history", "Run rules", "Reply format"):
                use = mono_b
                color = (190, 214, 255) if fill == NAVY else BLUE2
            d.text((margin + inner, yy), line, font=use, fill=color)
            yy += line_h
        return y0 + h

    y = panel(y, h_before, before_lines, NAVY, (219, 230, 247), NAVY, mono_f)
    cap = "exact result, handed over here"
    cap_f = font(SANS_BOLD, 18)
    y += 14
    d.text((margin + 8, y), cap, font=cap_f, fill=BLUE)
    y += 28
    y = panel(y, h_vm, vm_lines, SKY, INK, BLUE, mono_f)
    y += gap
    y = panel(y, h_after, after_lines, NAVY, (219, 230, 247), NAVY, mono_f)
    y += gap
    y = panel(y, h_answer, answer_lines, WARN_BG, INK, (224, 196, 138), mono_b)
    d.text((margin, y + 28), "Placeholder addresses. The canned answer blocks the withdrawal:", font=foot_f, fill=MUTE)
    d.text((margin, y + 58), "the amount is far above the usual size, and the destination was never seen.", font=foot_f, fill=MUTE)
    im.save(path, "PNG")
    print("wrote", path, im.size)


if __name__ == "__main__":
    draw_handoff(OUT / "judge-handoff.png")
    draw_prompt_card(OUT / "judge-prompt-card.png")
