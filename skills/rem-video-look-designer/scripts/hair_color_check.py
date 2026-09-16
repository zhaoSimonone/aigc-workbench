#!/usr/bin/env python3
"""量化比对发色:统计蓝色系头发像素的色相/饱和度/亮区明度,客观比对生成图与锚点。

用法:
    python scripts/hair_color_check.py IMAGE [IMAGE ...]

输出:中位色相 hue、中位饱和度 med_S、受光发冠明度 亮区V(p75)、高饱区S(p90)。
蕾姆假发蓝的目标带(以三张锚点实测):hue 0.59-0.63,亮区V 0.76-0.89,
高饱区S ~0.34。生成图亮区V 低于锚点带约 0.2 即发灰/发暗(常见失败模式),
明度饱和但色相正确为合格;全量中位数会被阴影发丝拉低,不要用其判定。
"""

from __future__ import annotations

import colorsys
import sys
from PIL import Image


def hair_stats(path: str) -> dict | None:
    img = Image.open(path).convert("RGB").resize((256, 455))
    px = list(img.getdata())
    vals: list[tuple[float, float, float]] = []
    for r, g, b in px:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        # 蓝色系: hue 0.50-0.70,排除低饱和背景与肤色
        if 0.50 <= h <= 0.70 and s > 0.10 and v > 0.20:
            vals.append((h, s, v))
    if not vals:
        return None
    vals.sort(key=lambda x: x[2])
    n = len(vals)
    med_h = sorted(v[0] for v in vals)[n // 2]
    med_s = sorted(v[1] for v in vals)[n // 2]
    p75_v = vals[int(n * 0.75)][2]
    p90_s = sorted(v[1] for v in vals)[int(n * 0.90)]
    return {
        "blue_pix_pct": round(100 * n / len(px)),
        "hue": round(med_h, 2),
        "med_S": round(med_s, 2),
        "lit_V_p75": round(p75_v, 2),
        "sat_S_p90": round(p90_s, 2),
    }


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
