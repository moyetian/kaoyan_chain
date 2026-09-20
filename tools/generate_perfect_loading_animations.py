# -*- coding: utf-8 -*-
"""
生成考研学习chain官方完美无穿模加载动图 (完美修复猫耳朵分离旋转问题)
"""

import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = ROOT / "docs" / "assets" / "c2_cat_refined_serene.jpg"
ASSETS_DIR = ROOT / "docs" / "assets"

def make_transparent(img_rgba: Image.Image, threshold=245, softness=15) -> Image.Image:
    """去除纯白背景，保留抗锯齿平滑边缘"""
    arr = np.array(img_rgba, dtype=np.float32)
    rgb = arr[:, :, :3]
    dist_from_white = np.sqrt(np.sum((255.0 - rgb) ** 2, axis=2))
    alpha = np.clip((dist_from_white - (255 - threshold)) / softness, 0.0, 1.0) * 255.0
    arr[:, :, 3] = alpha
    return Image.fromarray(arr.astype(np.uint8), mode="RGBA")

def build_perfect_layers():
    """使用 OpenCV 连通域精确分离：100% 完整猫咪前景层 与 纯外圈链条层"""
    # 兼容 Windows 中文路径
    src_pil = Image.open(SRC_PATH).convert("RGB")
    src_rgb = np.array(src_pil)
    src_bgr = cv2.cvtColor(src_rgb, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh)

    # 属于猫咪本体的连通域（包含大写 C 身体、头部轮廓、耳朵内外、五官细节与尾巴）
    cat_comps = {7, 9, 11, 12, 14, 15, 16, 17, 18, 26}

    # 1. 建立精确猫咪蒙版
    cat_mask = np.zeros_like(thresh)
    for c in cat_comps:
        cat_mask[labels == c] = 255

    # 填补猫脸白色内部孔洞（确保猫脸为实心白色前景，不会透出底层旋转的链条）
    head_sub = cat_mask[180:430, 200:450].copy()
    cnts, _ = cv2.findContours(head_sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(head_sub, cnts, -1, 255, -1)
    cat_mask[180:430, 200:450] = head_sub

    # 2. 建立纯外圈链条蒙版（完全不含任何猫咪或耳朵像素）
    chain_mask = thresh.copy()
    chain_mask[cat_mask > 0] = 0

    # 3. 构造 RGBA 图层
    H, W, _ = src_rgb.shape
    cat_rgba = np.zeros((H, W, 4), dtype=np.uint8)
    cat_rgba[:, :, :3] = src_rgb
    cat_rgba[:, :, 3] = np.where(cat_mask > 0, 255, 0).astype(np.uint8)

    chain_rgba = np.zeros((H, W, 4), dtype=np.uint8)
    chain_rgba[:, :, :3] = src_rgb
    chain_rgba[:, :, 3] = np.where(chain_mask > 0, 255, 0).astype(np.uint8)

    return Image.fromarray(src_rgb), Image.fromarray(cat_rgba), Image.fromarray(chain_rgba), chain_mask

def generate_energy_flow_gif(src_img, chain_mask):
    """
    方案一：【环形能量流光巡航 · 零穿模零撕裂（推荐旗舰版）】
    - 原图 100% 完整保留，猫咪和耳朵永远一体不动；
    - 一道灵动光束沿着外圈链条 360° 顺时针流动回旋；
    - 中心配以轻微安详呼吸感。
    """
    print("Generating Energy Flow Loading GIF (36 frames)...")
    H, W = 1024, 1024
    cx, cy = 512.0, 512.0
    y, x = np.ogrid[:H, :W]
    angles = np.arctan2(y - cy, x - cx) # [-pi, pi]
    
    num_frames = 36
    frames_opaque = []
    frames_trans = []

    src_arr = np.array(src_img, dtype=np.float32)
    chain_bin = (chain_mask > 0).astype(np.float32)

    for i in range(num_frames):
        # 顺时针流动的相位中心
        phi = - (2.0 * math.pi * i / num_frames)
        # 规范化到 [-pi, pi]
        diff = (angles - phi + math.pi) % (2.0 * math.pi) - math.pi
        
        # 光斑强度高斯衰减（波束跨度约 70 度）
        beam = np.exp(- (diff ** 2) / (2.0 * (0.65 ** 2)))
        glow = (beam * chain_bin)[:, :, np.newaxis]

        # 给链条增加高光和青绿/紫色发光色相叠加
        # 基础颜色加亮
        enhanced = src_arr.copy()
        # 高光提亮
        enhanced += glow * 110.0
        # 稍微加入青翠发光色调
        enhanced[:, :, 1] += glow[:, :, 0] * 45.0 # G
        enhanced[:, :, 0] += glow[:, :, 0] * 20.0 # R
        enhanced = np.clip(enhanced, 0.0, 255.0).astype(np.uint8)

        frame_img = Image.fromarray(enhanced, mode="RGB")

        # 结合小猫轻柔呼吸（微缩放 0.985 ~ 1.015）
        t = i / float(num_frames)
        scale = 1.0 + 0.015 * math.sin(2.0 * math.pi * t)
        nw, nh = int(W * scale), int(H * scale)
        scaled = frame_img.resize((nw, nh), Image.Resampling.BILINEAR)

        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        canvas.paste(scaled, ((W - nw) // 2, (H - nh) // 2))

        # 压缩到 256x256
        f_small = canvas.resize((256, 256), Image.Resampling.LANCZOS)
        frames_opaque.append(f_small)

        f_trans = make_transparent(f_small.convert("RGBA"))
        frames_trans.append(f_trans)

    frames_opaque[0].save(
        ASSETS_DIR / "loading_chain_cat.gif",
        format="GIF",
        save_all=True,
        append_images=frames_opaque[1:],
        duration=33,
        loop=0,
        optimize=True
    )

    frames_trans[0].save(
        ASSETS_DIR / "loading_chain_cat_transparent.gif",
        format="GIF",
        save_all=True,
        append_images=frames_trans[1:],
        duration=33,
        loop=0,
        disposal=2,
        optimize=False
    )
    print("Energy flow GIF saved successfully!")

def generate_layered_orbit_gif(cat_rgba, chain_rgba):
    """
    方案二：【正统图层分离旋转 · 猫咪耳朵绝对固定顶层】
    - 猫咪（连同完整耳朵、面容、C 身体）作为绝对顶层（Stationary Foreground）；
    - 外圈链条作为底层旋转，穿过小猫后方；
    - 绝无任何猫耳朵旋转到下方的 bug！
    """
    print("Generating Layered Orbit GIF with stationary cat on top...")
    H, W = 1024, 1024
    cx, cy = 512.0, 512.0
    num_frames = 36
    frames = []

    for i in range(num_frames):
        angle = -(i * 360.0 / num_frames) # 顺时针旋转
        t = i / float(num_frames)

        # 底层：旋转外圈链条
        rotated_chain = chain_rgba.rotate(angle, resample=Image.Resampling.BICUBIC, center=(cx, cy))

        # 顶层：完全静止的完整猫咪（耳朵 100% 固定在左上角！）
        # 轻微起伏呼吸
        scale = 1.0 + 0.015 * math.sin(2.0 * math.pi * t)
        nw, nh = int(W * scale), int(H * scale)
        scaled_cat = cat_rgba.resize((nw, nh), Image.Resampling.BILINEAR)

        # 画布合成：先画纯白背景，再叠底层旋转链条，最后覆盖猫咪前景
        canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        canvas.paste(rotated_chain, (0, 0), rotated_chain)

        ox = (W - nw) // 2
        oy = (H - nh) // 2
        canvas.paste(scaled_cat, (ox, oy), scaled_cat)

        f_small = canvas.resize((256, 256), Image.Resampling.LANCZOS)
        frames.append(f_small.convert("RGB"))

    frames[0].save(
        ASSETS_DIR / "loading_orbit.gif",
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=33,
        loop=0,
        optimize=True
    )
    print("Layered orbit GIF saved as loading_orbit.gif!")

def generate_clean_svg():
    """
    方案三：【修复版矢量 CSS 动画 SVG】
    修复之前 clipPath 粗暴将猫耳朵切断的错误，采用完整主体呼吸 + 极光光环环绕动效
    """
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="100%" height="100%">
  <defs>
    <style>
      @keyframes ky-breathe {
        0%, 100% { transform: scale(1.0); }
        50% { transform: scale(1.03); }
      }
      @keyframes ky-ring-glow {
        0%, 100% {
          filter: drop-shadow(0 0 4px rgba(124, 58, 237, 0.3));
          stroke-dashoffset: 0;
        }
        50% {
          filter: drop-shadow(0 0 12px rgba(52, 211, 153, 0.6));
          stroke-dashoffset: 180;
        }
      }
      .ky-logo-body {
        transform-origin: 128px 128px;
        animation: ky-breathe 2.4s ease-in-out infinite;
      }
      .ky-orbit-arc {
        transform-origin: 128px 128px;
        stroke-dasharray: 90 270;
        animation: ky-ring-glow 3s linear infinite;
      }
    </style>
  </defs>

  <!-- 完整无缺的主标（小猫耳朵 100% 完整） -->
  <g class="ky-logo-body">
    <image href="logo_transparent.png" x="16" y="16" width="224" height="224" />
  </g>

  <!-- 围绕外围的轻盈环形能量流动流光 -->
  <circle cx="128" cy="128" r="102" fill="none" stroke="url(#ky-grad)" stroke-width="3" stroke-linecap="round" class="ky-orbit-arc" />

  <linearGradient id="ky-grad" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="#7c3aed" stop-opacity="0.8" />
    <stop offset="100%" stop-color="#34d399" stop-opacity="0.9" />
  </linearGradient>
</svg>
'''
    (ASSETS_DIR / "loading_spinner.svg").write_text(svg_content, encoding="utf-8")
    print("Clean SVG saved as loading_spinner.svg!")

def main():
    src_img, cat_rgba, chain_rgba, chain_mask = build_perfect_layers()
    generate_energy_flow_gif(src_img, chain_mask)
    generate_layered_orbit_gif(cat_rgba, chain_rgba)
    generate_clean_svg()
    
    # 同步至 05-考研看板/docs/assets/
    dst_dir = ROOT / "05-考研看板" / "docs" / "assets"
    for name in ["loading_chain_cat.gif", "loading_chain_cat_transparent.gif", "loading_orbit.gif", "loading_spinner.svg"]:
        import shutil
        shutil.copy2(ASSETS_DIR / name, dst_dir / name)
    print("All animations synchronized to both directories!")

if __name__ == "__main__":
    main()
