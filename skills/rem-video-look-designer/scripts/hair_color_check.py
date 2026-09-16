#!/usr/bin/env python3
"""量化比对发色:统计图中蓝色系头发像素的中位 HSV,用于生成图与锚点图的客观比对。

用法:
    python scripts/hair_color_check.py IMAGE [IMAGE ...]

输出每张图蓝色系像素占比与中位饱和度(S)/明度(V)。QC 时先生成锚点基准值
(如 rem-warm-portrait.png),生成图的 S/V 与基准偏差超过约 ±0.08 即判发色
过亮/过暗/过饱和,应调整提示词后重生成,而不是仅凭目检。
"""

from __future__ import annotations

import colorsys
import sys
from PIL import Image


def hair_stats(path: str) -> dict | None:
    img = Image.open(path).convert("RGB").resize((256, 455))
    px = list(img.getdata())
    vals: list[tuple[float, float]] = []
    for r, g, b in px:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        # 蓝色系: hue 0.52-0.68,排除低饱和背景与肤色
        if 0.52 <= h <= 0.68 and s > 0.12 and v > 0.25:
            vals.append((s, v))
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    med = vals[n // 2]
    return {"blue_pix_pct": round(100 * n / len(px)), "median_S": round(med[0], 2), "median_V": round(med[1], 2)}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for path in sys.argv[1:]:
        try:
            stats = hair_stats(path)
        except Exception as exc:  # noqa: BLE001 - QC 工具,报错继续处理其余图片
            print(f"{path}: error {exc}")
            continue
        print(f"{path}: {stats if stats else 'no blue-dominant hair pixels found'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
