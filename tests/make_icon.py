"""生成 netboost 应用图标（多尺寸 .ico）。"""
import os
import sys

from PIL import Image, ImageDraw

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG = (23, 29, 37)
ACCENT = (47, 129, 247)
GREEN = (63, 185, 80)
WHITE = (230, 237, 243)


def draw_icon(size: int) -> Image.Image:
    """圆角方块 + 向下箭头 + 底部托盘线，突出'下载'语义。"""
    scale = 8  # 超采样后缩小，边缘更平滑
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角背景
    radius = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=BG)

    # 内侧亮点（渐变感）
    d.rounded_rectangle([int(s * 0.06), int(s * 0.06), int(s * 0.94), int(s * 0.94)],
                        radius=int(radius * 0.82), fill=(26, 33, 43, 255))

    # 向下箭头（杆）
    cx = s // 2
    stem_w = int(s * 0.13)
    stem_top = int(s * 0.20)
    stem_bot = int(s * 0.52)
    d.rounded_rectangle([cx - stem_w // 2, stem_top, cx + stem_w // 2, stem_bot],
                        radius=stem_w // 2, fill=ACCENT)

    # 箭头头部（三角）
    head_w = int(s * 0.34)
    head_top = int(s * 0.47)
    head_bot = int(s * 0.70)
    d.polygon([(cx - head_w // 2, head_top), (cx + head_w // 2, head_top), (cx, head_bot)],
              fill=ACCENT)

    # 底部托盘（下载完成的落点）
    bar_w = int(s * 0.44)
    bar_h = int(s * 0.075)
    bar_y = int(s * 0.78)
    d.rounded_rectangle([cx - bar_w // 2, bar_y, cx + bar_w // 2, bar_y + bar_h],
                        radius=bar_h // 2, fill=GREEN)

    return img.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw_icon(n) for n in sizes]
    ico = os.path.join(HERE, "app.ico")
    imgs[-1].save(ico, format="ICO", sizes=[(n, n) for n in sizes])
    print("图标已生成:", ico, os.path.getsize(ico), "B")

    # 同时导出一张预览 PNG 方便查看
    preview = os.path.join(HERE, "icon-preview.png")
    sheet = Image.new("RGBA", (sum(sizes) + 20 * len(sizes), 256), (15, 20, 25, 255))
    x = 10
    for n, im in zip(sizes, imgs):
        sheet.paste(im, (x, 128 - n // 2), im)
        x += n + 20
    sheet.save(preview)
    print("预览:", preview)


if __name__ == "__main__":
    main()
