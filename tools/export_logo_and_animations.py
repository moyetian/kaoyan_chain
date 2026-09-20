# -*- coding: utf-8 -*-
"""
生成考研学习chain官方 Logo 多规格产物与加载动图 (GIF / SVG / ICO / PNG)
"""

import math
from pathlib import Path
from PIL import Image
import numpy as np

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

def main():
    print(f"Loading source: {SRC_PATH}")
    src_img = Image.open(SRC_PATH).convert("RGBA")
    
    # 1. 基础尺寸与透明版
    print("Generating static Logo formats...")
    logo_512 = src_img.resize((512, 512), Image.Resampling.LANCZOS)
    logo_512.save(ASSETS_DIR / "logo.png", format="PNG", optimize=True)
    
    logo_trans_512 = make_transparent(logo_512)
    logo_trans_512.save(ASSETS_DIR / "logo_transparent.png", format="PNG", optimize=True)
    
    # 2. Favicon 图标与 ICO
    print("Generating Favicon & ICO...")
    fav_64 = logo_trans_512.resize((64, 64), Image.Resampling.LANCZOS)
    fav_64.save(ASSETS_DIR / "favicon.png", format="PNG", optimize=True)
    
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_imgs = [logo_trans_512.resize(s, Image.Resampling.LANCZOS) for s in ico_sizes]
    ico_imgs[0].save(
        ASSETS_DIR / "favicon.ico",
        format="ICO",
        sizes=ico_sizes,
        append_images=ico_imgs[1:]
    )
    
    # 3. 分离「内圈 C-Cat」与「外圈旋转链环」
    print("Preparing animation layers...")
    arr = np.array(src_img, dtype=np.float32)
    H, W, _ = arr.shape
    cy, cx = 512.0, 512.0
    y, x = np.ogrid[:H, :W]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    
    # 软分割蒙版：r <= 275 为猫咪，r >= 295 为链条
    chain_weight = np.clip((r - 275.0) / 20.0, 0.0, 1.0)[:, :, np.newaxis]
    cat_weight = 1.0 - chain_weight
    white_bg = np.ones_like(arr) * 255.0
    
    chain_arr = arr * chain_weight + white_bg * cat_weight
    chain_layer = Image.fromarray(chain_arr.astype(np.uint8), mode="RGBA")
    
    cat_arr = arr * cat_weight + white_bg * chain_weight
    cat_layer = Image.fromarray(cat_arr.astype(np.uint8), mode="RGBA")
    
    # 4. 生成顺滑旋转与呼吸动图 (36 帧，完整 360° 顺时针旋转，30 FPS)
    print("Rendering 36 frames for loading_chain_cat.gif...")
    num_frames = 36
    frames_opaque = []
    frames_trans = []
    
    for i in range(num_frames):
        angle = -(i * 360.0 / num_frames) # 负角度顺时针旋转
        t = i / float(num_frames)
        
        # 外环平滑旋转
        rotated_chain = chain_layer.rotate(angle, resample=Image.Resampling.BICUBIC, center=(cx, cy))
        
        # 内圈小猫呼吸微动：周期 1（scale 0.98 ~ 1.025）
        scale = 1.0 + 0.025 * math.sin(2.0 * math.pi * t)
        nw, nh = int(W * scale), int(H * scale)
        scaled_cat = cat_layer.resize((nw, nh), Image.Resampling.BILINEAR)
        
        cat_canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        ox = (W - nw) // 2
        oy = (H - nh) // 2
        cat_canvas.paste(scaled_cat, (ox, oy))
        
        c_arr = np.minimum(np.array(rotated_chain), np.array(cat_canvas))
        composed = Image.fromarray(c_arr, mode="RGBA")
        
        f_small = composed.resize((256, 256), Image.Resampling.LANCZOS)
        frames_opaque.append(f_small.convert("RGB"))
        
        f_trans = make_transparent(f_small)
        frames_trans.append(f_trans)
        
    print("Saving loading_chain_cat.gif...")
    frames_opaque[0].save(
        ASSETS_DIR / "loading_chain_cat.gif",
        format="GIF",
        save_all=True,
        append_images=frames_opaque[1:],
        duration=33, # ~30fps
        loop=0,
        optimize=True
    )
    
    print("Saving loading_chain_cat_transparent.gif...")
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
    
    # 5. 生成现代矢量 CSS 动效 SVG (超轻量 < 8KB，无损 60fps)
    print("Generating modern SVG loading animation...")
    svg_content = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="100%" height="100%">
  <defs>
    <style>
      @keyframes ky-chain-spin {{
        from {{ transform: rotate(0deg); }}
        to {{ transform: rotate(360deg); }}
      }}
      @keyframes ky-cat-breathe {{
        0%, 100% {{ transform: scale(1.0); }}
        50% {{ transform: scale(1.04); }}
      }}
      @keyframes ky-glow-pulse {{
        0%, 100% {{ filter: drop-shadow(0 0 4px rgba(124, 58, 237, 0.2)); }}
        50% {{ filter: drop-shadow(0 0 14px rgba(52, 211, 153, 0.45)); }}
      }}
      .ky-chain-track {{
        transform-origin: 128px 128px;
        animation: ky-chain-spin 4s linear infinite;
      }}
      .ky-cat-center {{
        transform-origin: 128px 128px;
        animation: ky-cat-breathe 2.4s ease-in-out infinite, ky-glow-pulse 2.4s ease-in-out infinite;
      }}
    </style>
  </defs>
  
  <g class="ky-chain-track">
    <image href="logo_transparent.png" x="0" y="0" width="256" height="256" clip-path="url(#chain-clip)" />
  </g>
  
  <g class="ky-cat-center">
    <image href="logo_transparent.png" x="0" y="0" width="256" height="256" clip-path="url(#cat-clip)" />
  </g>
  
  <clipPath id="cat-clip">
    <circle cx="128" cy="128" r="70" />
  </clipPath>
  <clipPath id="chain-clip">
    <path d="M 0 0 H 256 V 256 H 0 Z M 128 58 A 70 70 0 1 0 128 198 A 70 70 0 1 0 128 58 Z" fill-rule="evenodd" />
  </clipPath>
</svg>
'''
    (ASSETS_DIR / "loading_spinner.svg").write_text(svg_content, encoding="utf-8")
    
    print("All assets successfully generated in docs/assets/!")

if __name__ == "__main__":
    main()
