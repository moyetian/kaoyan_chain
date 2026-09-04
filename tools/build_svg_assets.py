# -*- coding: utf-8 -*-
"""
Generate SVG diagram assets for README.md:
1. docs/assets/intelligence_architecture.svg
2. docs/assets/school_comparator_matrix.svg
3. docs/assets/dashboard_5tabs_architecture.svg
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "docs" / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# 1. intelligence_architecture.svg
# ─────────────────────────────────────────────────────────────
SVG1 = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560" width="100%" height="100%">
  <defs>
    <linearGradient id="bg1" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#070a12" />
      <stop offset="50%" stop-color="#0d1322" />
      <stop offset="100%" stop-color="#080c18" />
    </linearGradient>
    <linearGradient id="sGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#064e3b" />
      <stop offset="100%" stop-color="#022c22" />
    </linearGradient>
    <linearGradient id="aGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e3a8a" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <linearGradient id="cGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#4c1d95" />
      <stop offset="100%" stop-color="#2e1065" />
    </linearGradient>
    <filter id="shadow1" x="-5%" y="-5%" width="110%" height="110%">
      <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity="0.4" />
    </filter>
  </defs>

  <style>
    .t-main { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 800; fill: #f8fafc; font-size: 24px; }
    .t-sub { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; fill: #94a3b8; font-size: 13px; }
    .b-title { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 700; font-size: 13.5px; }
    .b-desc { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-size: 11px; fill: #94a3b8; }
    .badge { font-family: "JetBrains Mono", Consolas, monospace; font-size: 10px; font-weight: 700; }
  </style>

  <rect width="960" height="560" rx="16" fill="url(#bg1)" stroke="#1e293b" stroke-width="1.5" />
  
  <!-- 网格 -->
  <path d="M 0 80 L 960 80 M 0 175 L 960 175 M 0 325 L 960 325 M 0 435 L 960 435" stroke="#334155" stroke-opacity="0.15" stroke-width="1" />
  <path d="M 160 0 L 160 560 M 320 0 L 320 560 M 480 0 L 480 560 M 640 0 L 640 560 M 800 0 L 800 560" stroke="#334155" stroke-opacity="0.15" stroke-width="1" />

  <!-- 标题 -->
  <g transform="translate(480, 42)" text-anchor="middle">
    <text class="t-main" y="0">KaoYan Intelligence · 考研招考情报与权威证据链中枢</text>
    <text class="t-sub" y="24">Search = Discovery · Official Page = Evidence · LLM = Reasoner · 全国 800+ 高校自适应全覆盖</text>
  </g>

  <!-- 1. 输入与实体解析层 -->
  <g transform="translate(40, 85)">
    <rect width="880" height="62" rx="10" fill="#0f172a" stroke="#334155" stroke-width="1.2" filter="url(#shadow1)" />
    <g transform="translate(20, 18)">
      <rect width="170" height="26" rx="5" fill="#1e293b" stroke="#3b82f6" stroke-width="1" />
      <text x="85" y="17" text-anchor="middle" fill="#93c5fd" font-size="11.5" font-weight="700">🎯 学员输入 (高校/专业/代码)</text>
    </g>
    <path d="M 205 31 L 235 31" stroke="#60a5fa" stroke-width="2" />
    <g transform="translate(245, 10)">
      <rect width="380" height="42" rx="6" fill="#172554" stroke="#2563eb" stroke-width="1.2" />
      <text x="15" y="18" fill="#bfdbfe" font-size="12" font-weight="700">🏛️ 高校实体解析器 &amp; 通用自适应推断器</text>
      <text x="15" y="33" fill="#93c5fd" font-size="10.5">55+ 重点高校注册表 ＋ 800+ 双非自适应推导 (省市识别 / A区/B区判定 / 代码补全)</text>
    </g>
    <g transform="translate(640, 10)">
      <rect width="220" height="42" rx="6" fill="#1f1538" stroke="#8b5cf6" stroke-width="1.2" />
      <text x="15" y="18" fill="#ddd6fe" font-size="12" font-weight="700">🌲 四级有向官方站点图谱</text>
      <text x="15" y="33" fill="#a78bfa" font-size="10.5">官网 → 研究生院 → 招生网 → 学院</text>
    </g>
  </g>

  <!-- 2. 三源采集矩阵 -->
  <g transform="translate(40, 170)">
    <!-- S级 -->
    <g transform="translate(0, 0)">
      <rect width="275" height="135" rx="10" fill="url(#sGrad)" stroke="#059669" stroke-width="1.2" filter="url(#shadow1)" />
      <rect x="12" y="12" width="68" height="20" rx="4" fill="#065f46" />
      <text x="46" y="26" text-anchor="middle" fill="#34d399" class="badge">S 级权威</text>
      <text x="88" y="27" fill="#6ee7b7" class="b-title">教育部研招网直连</text>
      <text x="15" y="55" class="b-desc">• yz.chsi.com.cn 专业目录参数化对接</text>
      <text x="15" y="75" class="b-desc">• 锁定全国统考科目 (101/204/302/408)</text>
      <text x="15" y="95" class="b-desc">• 提取研招单位、院系所、研究方向</text>
      <text x="15" y="115" class="b-desc" fill="#10b981">• 置信度: 100% ｜ 裁决性最高基准</text>
    </g>
    <!-- A级 -->
    <g transform="translate(295, 0)">
      <rect width="290" height="135" rx="10" fill="url(#aGrad)" stroke="#3b82f6" stroke-width="1.2" filter="url(#shadow1)" />
      <rect x="12" y="12" width="68" height="20" rx="4" fill="#1e40af" />
      <text x="46" y="26" text-anchor="middle" fill="#60a5fa" class="badge">A 级官方</text>
      <text x="88" y="27" fill="#93c5fd" class="b-title">高校研究生院/招生网</text>
      <text x="15" y="55" class="b-desc">• 探测 Robots.txt &amp; Sitemap.xml 简章链接</text>
      <text x="15" y="75" class="b-desc">• 智能正则抽取拟招人数与自命题科目</text>
      <text x="15" y="95" class="b-desc" fill="#38bdf8">• 📑 PDF 专业目录深度解析 (表格/代码)</text>
      <text x="15" y="115" class="b-desc" fill="#93c5fd">• 置信度: 90%~95% ｜ 官方一手事实</text>
    </g>
    <!-- C级 -->
    <g transform="translate(605, 0)">
      <rect width="275" height="135" rx="10" fill="url(#cGrad)" stroke="#8b5cf6" stroke-width="1.2" filter="url(#shadow1)" />
      <rect x="12" y="12" width="68" height="20" rx="4" fill="#5b21b6" />
      <text x="46" y="26" text-anchor="middle" fill="#c084fc" class="badge">C 级民间</text>
      <text x="88" y="27" fill="#c4b5fd" class="b-title">三大社媒实名直通车</text>
      <text x="15" y="55" class="b-desc">• 💡 知乎：就读体验、导师风评、实验室</text>
      <text x="15" y="75" class="b-desc">• 📺 B站：高分复盘、复试回忆、真题拆解</text>
      <text x="15" y="95" class="b-desc">• 📕 小红书：压分排查、一志愿保护避坑</text>
      <text x="15" y="115" class="b-desc" fill="#e879f9">• 置信度: 30% ｜ 辅助研判与避雷防雷</text>
    </g>
  </g>

  <!-- 3. 证据裁决与监控中枢 -->
  <g transform="translate(40, 325)">
    <rect width="880" height="75" rx="10" fill="#09101f" stroke="#22d3ee" stroke-width="1.2" filter="url(#shadow1)" />
    <g transform="translate(20, 15)">
      <rect width="180" height="45" rx="6" fill="#132338" stroke="#0284c7" stroke-width="1" />
      <text x="12" y="20" fill="#38bdf8" font-size="11.5" font-weight="700">🔒 年份锁定机制 (Year Lock)</text>
      <text x="12" y="36" fill="#94a3b8" font-size="10">锁死 2027，历史旧数据打 OUTDATED 标</text>
    </g>
    <g transform="translate(220, 15)">
      <rect width="210" height="45" rx="6" fill="#2d1525" stroke="#db2777" stroke-width="1" />
      <text x="12" y="20" fill="#f472b6" font-size="11.5" font-weight="700">⚖️ 多源冲突仲裁 (Resolver)</text>
      <text x="12" y="36" fill="#94a3b8" font-size="10">学院微调与研招网冲突打 CONFLICT 标</text>
    </g>
    <g transform="translate(450, 15)">
      <rect width="200" height="45" rx="6" fill="#1c1917" stroke="#ea580c" stroke-width="1" />
      <text x="12" y="20" fill="#fb923c" font-size="11.5" font-weight="700">📡 简章指纹雷达 (Watcher)</text>
      <text x="12" y="36" fill="#94a3b8" font-size="10">SHA256 哈希比对，第一时间告警新简章</text>
    </g>
    <g transform="translate(670, 15)">
      <rect width="190" height="45" rx="6" fill="#14231b" stroke="#16a34a" stroke-width="1" />
      <text x="12" y="20" fill="#4ade80" font-size="11.5" font-weight="700">📊 学情量化诊断 (Gap Analysis)</text>
      <text x="12" y="36" fill="#94a3b8" font-size="10">联动目标分测算复试安全缓冲垫</text>
    </g>
  </g>

  <!-- 4. 命令行输出层 -->
  <g transform="translate(40, 425)">
    <rect width="880" height="95" rx="10" fill="#0f172a" stroke="#334155" stroke-width="1.2" filter="url(#shadow1)" />
    <g transform="translate(20, 15)">
      <rect width="265" height="65" rx="6" fill="#1e293b" stroke="#38bdf8" stroke-width="1" />
      <text x="15" y="22" fill="#38bdf8" font-size="12" font-weight="700">📋 ky admission &lt;高校&gt; [专业]</text>
      <text x="15" y="40" fill="#cbd5e1" font-size="10.5">S/A级信源证据链卡片 ＋ 04-专业课/ 研报</text>
      <text x="15" y="56" fill="#94a3b8" font-size="9.5">精准锁定初试统考/自命题科目与招生人数</text>
    </g>
    <g transform="translate(305, 15)">
      <rect width="270" height="65" rx="6" fill="#1e293b" stroke="#a855f7" stroke-width="1" />
      <text x="15" y="22" fill="#c084fc" font-size="12" font-weight="700">⚔️ ky compare &lt;校1&gt; &lt;校2&gt; [专业]</text>
      <text x="15" y="40" fill="#cbd5e1" font-size="10.5">双校 6 维横向深度对标 ＋ 落地 Markdown</text>
      <text x="15" y="56" fill="#94a3b8" font-size="9.5">408差异 / 国家A区B区 / 复试线 / 一志愿保护</text>
    </g>
    <g transform="translate(595, 15)">
      <rect width="265" height="65" rx="6" fill="#1e293b" stroke="#f59e0b" stroke-width="1" />
      <text x="15" y="22" fill="#fbbf24" font-size="12" font-weight="700">🔥 ky watch &amp; ky today</text>
      <text x="15" y="40" fill="#cbd5e1" font-size="10.5">招生动态监控雷达 ＋ 晨报突发情报速递</text>
      <text x="15" y="56" fill="#94a3b8" font-size="9.5">每天起床第一眼捕获最新简章与考纲异动</text>
    </g>
  </g>
</svg>"""

# ─────────────────────────────────────────────────────────────
# 2. school_comparator_matrix.svg
# ─────────────────────────────────────────────────────────────
SVG2 = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560" width="100%" height="100%">
  <defs>
    <linearGradient id="bg2" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#080b14" />
      <stop offset="50%" stop-color="#0f172a" />
      <stop offset="100%" stop-color="#0a0e1a" />
    </linearGradient>
    <linearGradient id="schoolAGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <linearGradient id="vsGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#dc2626" />
      <stop offset="100%" stop-color="#991b1b" />
    </linearGradient>
    <filter id="shadow2" x="-5%" y="-5%" width="110%" height="110%">
      <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity="0.35" />
    </filter>
  </defs>

  <style>
    .t-main2 { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 800; fill: #f8fafc; font-size: 24px; }
    .t-sub2 { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; fill: #94a3b8; font-size: 13px; }
    .sch-name { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 800; font-size: 17px; }
    .dim-label { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 700; font-size: 12px; fill: #cbd5e1; }
    .val-text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-size: 11.5px; fill: #94a3b8; }
    .val-emp { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 700; font-size: 11.5px; }
  </style>

  <rect width="960" height="560" rx="16" fill="url(#bg2)" stroke="#1e293b" stroke-width="1.5" />

  <!-- 顶部标题 -->
  <g transform="translate(480, 40)" text-anchor="middle">
    <text class="t-main2" y="0">ky compare · 考研双校招考横向深度对标矩阵</text>
    <text class="t-sub2" y="24">初试统考 408 vs 自命题差异 · 国家 A 区/B 区与自划线 · 历年复试线走向 · 一志愿保护与避坑研判</text>
  </g>

  <!-- ══════════════ 核心对标大盘 ══════════════ -->
  <g transform="translate(40, 80)">
    <!-- 顶栏：两校头部与 VS 徽标 -->
    <g transform="translate(0, 0)">
      <!-- 高校 A 头部 -->
      <rect x="0" y="0" width="370" height="56" rx="8" fill="#172554" stroke="#2563eb" stroke-width="1.2" />
      <text x="20" y="26" class="sch-name" fill="#60a5fa">【高校 A】华中科技大学</text>
      <text x="20" y="44" font-size="11" fill="#93c5fd">院校代码: 10487 ｜ 985 / 211 / 自划线 ｜ 湖北武汉</text>

      <!-- VS 中枢徽章 -->
      <g transform="translate(415, 6)">
        <rect width="50" height="44" rx="8" fill="url(#vsGrad)" filter="url(#shadow2)" />
        <text x="25" y="28" text-anchor="middle" fill="#fff" font-family="sans-serif" font-weight="900" font-size="16">VS</text>
      </g>

      <!-- 高校 B 头部 -->
      <rect x="510" y="0" width="370" height="56" rx="8" fill="#1e1b4b" stroke="#7c3aed" stroke-width="1.2" />
      <text x="530" y="26" class="sch-name" fill="#a78bfa">【高校 B】东莞理工 / 河南科大</text>
      <text x="530" y="44" font-size="11" fill="#c4b5fd">院校代码: 11845 / 10464 ｜ 省属重点 / 国家A区线</text>
    </g>

    <!-- 6 大对标维度行 -->
    <!-- 行 1: 办学层次与自划线 -->
    <g transform="translate(0, 68)">
      <rect width="880" height="48" rx="6" fill="#0f172a" stroke="#1e293b" stroke-width="1" />
      <rect x="375" y="10" width="130" height="28" rx="4" fill="#1e293b" />
      <text x="440" y="28" text-anchor="middle" class="dim-label">办学层次 &amp; 分区</text>
      
      <text x="20" y="24" class="val-emp" fill="#38bdf8">34所自主划线 · 985/211/双一流A类</text>
      <text x="20" y="39" class="val-text">执行学校单独划定的初试复试单科与总分线</text>

      <text x="530" y="24" class="val-emp" fill="#c084fc">省属重点公办高校 · 国家一区线 (A区)</text>
      <text x="530" y="39" class="val-text">执行教育部统考国家线，过线即享复试与调剂资格</text>
    </g>

    <!-- 行 2: 初试专业课 -->
    <g transform="translate(0, 124)">
      <rect width="880" height="48" rx="6" fill="#09101f" stroke="#1e293b" stroke-width="1" />
      <rect x="375" y="10" width="130" height="28" rx="4" fill="#1e293b" />
      <text x="440" y="28" text-anchor="middle" class="dim-label">初试科目特征</text>
      
      <text x="20" y="24" class="val-emp" fill="#10b981">全国统考 408 计算机学科专业基础</text>
      <text x="20" y="39" class="val-text">全国统考，题库高度公开透明，复习通用性极高</text>

      <text x="530" y="24" class="val-emp" fill="#f59e0b">院校自命题 (801/812) 或部分 408</text>
      <text x="530" y="39" class="val-text">题型多为本校命制，历年真题不公开，需学长学姐回忆</text>
    </g>

    <!-- 行 3: 复试线走势 -->
    <g transform="translate(0, 180)">
      <rect width="880" height="48" rx="6" fill="#0f172a" stroke="#1e293b" stroke-width="1" />
      <rect x="375" y="10" width="130" height="28" rx="4" fill="#1e293b" />
      <text x="440" y="28" text-anchor="middle" class="dim-label">历年复试线走势</text>
      
      <text x="20" y="24" class="val-emp" fill="#f43f5e">高位厮杀：330~355 分 (学硕/专硕)</text>
      <text x="20" y="39" class="val-text">单科线卡死 50~55 分，初试高分密集扎堆</text>

      <text x="530" y="24" class="val-emp" fill="#34d399">稳妥托底：通常贴国家线 (265~275 分)</text>
      <text x="530" y="39" class="val-text">300+ 即享绝对高位优势，最大死穴为严防单科不过线</text>
    </g>

    <!-- 行 4: 一志愿保护机制 -->
    <g transform="translate(0, 236)">
      <rect width="880" height="48" rx="6" fill="#09101f" stroke="#1e293b" stroke-width="1" />
      <rect x="375" y="10" width="130" height="28" rx="4" fill="#1e293b" />
      <text x="440" y="28" text-anchor="middle" class="dim-label">一志愿保护机制</text>
      
      <text x="20" y="24" class="val-emp" fill="#facc15">🌟 业内公认高度保护一志愿！</text>
      <text x="20" y="39" class="val-text">一志愿名单公示前不接收校外调剂，复试极其公平</text>

      <text x="530" y="24" class="val-emp" fill="#fb923c">需核查调剂细则 ＆ 排查压分恶名</text>
      <text x="530" y="39" class="val-text">优秀双非上线全收；极少数学院需排查留名额给调剂</text>
    </g>

    <!-- 行 5: 核心避坑警示 -->
    <g transform="translate(0, 292)">
      <rect width="880" height="48" rx="6" fill="#0f172a" stroke="#1e293b" stroke-width="1" />
      <rect x="375" y="10" width="130" height="28" rx="4" fill="#1e293b" />
      <text x="440" y="28" text-anchor="middle" class="dim-label">核心避坑红黑榜</text>
      
      <text x="20" y="24" class="val-emp" fill="#f87171">⚠️ 复试机试要求极高，不可初试后躺平</text>
      <text x="20" y="39" class="val-text">极重编程动手实力与算法考核，选导师需提前打听</text>

      <text x="530" y="24" class="val-emp" fill="#f87171">⚠️ 警惕 9 月初考纲突改统考 408</text>
      <text x="530" y="39" class="val-text">自命题若突然改考 408 极易崩盘；死守英语数学单科线</text>
    </g>
  </g>

  <!-- ══════════════ 底部：私教量化择校与提分处方卡 ══════════════ -->
  <g transform="translate(40, 435)">
    <rect width="880" height="95" rx="10" fill="#0b1329" stroke="#3b82f6" stroke-width="1.2" filter="url(#shadow2)" />
    <g transform="translate(20, 16)">
      <rect width="180" height="22" rx="4" fill="#1d4ed8" />
      <text x="90" y="15" text-anchor="middle" fill="#fff" font-size="11" font-weight="700">🎯 私教量化择校处方 (Gap Analysis)</text>
      
      <text x="0" y="44" fill="#e2e8f0" font-size="12" font-weight="600">联动学员 370+ 目标分：</text>
      <text x="135" y="44" fill="#94a3b8" font-size="11.5">冲刺【华科】具备 15~25 分复试安全缓冲垫，数学(110+)与 408(120+)合计需稳拿 230+ 分基本盘；</text>
      <text x="0" y="65" fill="#94a3b8" font-size="11.5">报考【东莞理工/河南科大】初试总分处于碾压级优势（超出复试线 70~100 分），战略核心在于【严守单科线】＋【真题回忆卷还原】！</text>
    </g>
  </g>
</svg>"""

# ─────────────────────────────────────────────────────────────
# 3. dashboard_5tabs_architecture.svg
# ─────────────────────────────────────────────────────────────
SVG3 = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560" width="100%" height="100%">
  <defs>
    <linearGradient id="bg3" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#060913" />
      <stop offset="50%" stop-color="#0b1120" />
      <stop offset="100%" stop-color="#080c16" />
    </linearGradient>
    <linearGradient id="tabGrad1" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <filter id="shadow3" x="-5%" y="-5%" width="110%" height="110%">
      <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity="0.3" />
    </filter>
  </defs>

  <style>
    .t-main3 { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 800; fill: #f8fafc; font-size: 24px; }
    .t-sub3 { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; fill: #94a3b8; font-size: 13px; }
    .tab-t { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-weight: 700; font-size: 14px; }
    .tab-d { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; font-size: 11px; fill: #94a3b8; }
    .feat-tag { font-family: "JetBrains Mono", Consolas, monospace; font-size: 9.5px; font-weight: 700; }
  </style>

  <rect width="960" height="560" rx="16" fill="url(#bg3)" stroke="#1e293b" stroke-width="1.5" />

  <!-- 标题 -->
  <g transform="translate(480, 42)" text-anchor="middle">
    <text class="t-main3" y="0">考研全科自测看板 · 五大核心交互页签与考纲图谱架构</text>
    <text class="t-sub3" y="24">单文件静态免部署 · 移动端 PWA 丝滑自测 · 7日完成率趋势 · 考纲全景图谱 · 👁️ 毛玻璃遮罩自测</text>
  </g>

  <!-- ══════════════ 五大页签卡片矩阵 ══════════════ -->
  <g transform="translate(40, 85)">
    <!-- 页签 1: 今日 (Today) -->
    <g transform="translate(0, 0)">
      <rect width="168" height="310" rx="10" fill="url(#tabGrad1)" stroke="#38bdf8" stroke-width="1.2" filter="url(#shadow3)" />
      <rect x="12" y="12" width="144" height="28" rx="6" fill="#0284c7" />
      <text x="84" y="31" text-anchor="middle" fill="#fff" class="tab-t">📋 今日 (Today)</text>
      
      <g transform="translate(12, 52)">
        <rect width="144" height="42" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#38bdf8" class="feat-tag">⏱️ 8.5h 时间预算条</text>
        <text x="8" y="32" class="tab-d">四科时长动态进度柱</text>
      </g>
      <g transform="translate(12, 102)">
        <rect width="144" height="50" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#38bdf8" class="feat-tag">🎯 薄弱攻坚清单</text>
        <text x="8" y="32" class="tab-d">中值定理/长难句</text>
        <text x="8" y="44" class="tab-d">支持即时打勾回写</text>
      </g>
      <g transform="translate(12, 160)">
        <rect width="144" height="80" rx="5" fill="#2d1525" stroke="#db2777" stroke-width="0.8" />
        <text x="8" y="18" fill="#f472b6" class="feat-tag">🔥 突发简章速递</text>
        <text x="8" y="34" class="tab-d" fill="#fbcfe8">• 研究生院简章变动</text>
        <text x="8" y="50" class="tab-d" fill="#fbcfe8">• 专业大纲调整提醒</text>
        <text x="8" y="68" class="tab-d" fill="#fbcfe8">• 置顶晨报红字告警</text>
      </g>
      <g transform="translate(12, 250)">
        <text x="72" y="24" text-anchor="middle" fill="#94a3b8" font-size="10.5">倒计时毫秒感知</text>
        <text x="72" y="40" text-anchor="middle" fill="#38bdf8" font-size="11" font-weight="700">距初试 106 天</text>
      </g>
    </g>

    <!-- 页签 2: 必背 (Flashcards) -->
    <g transform="translate(178, 0)">
      <rect width="168" height="310" rx="10" fill="url(#tabGrad1)" stroke="#10b981" stroke-width="1.2" filter="url(#shadow3)" />
      <rect x="12" y="12" width="144" height="28" rx="6" fill="#059669" />
      <text x="84" y="31" text-anchor="middle" fill="#fff" class="tab-t">🧠 必背 (Cards)</text>

      <g transform="translate(12, 52)">
        <rect width="144" height="42" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#34d399" class="feat-tag">🗂️ 四科 3D 翻转卡</text>
        <text x="8" y="32" class="tab-d">定理/积分表/单词句型</text>
      </g>
      <g transform="translate(12, 102)">
        <rect width="144" height="66" rx="5" fill="#064e3b" stroke="#10b981" stroke-width="1" />
        <text x="8" y="18" fill="#6ee7b7" class="feat-tag">👁️ 独创遮罩自测</text>
        <text x="8" y="34" class="tab-d" fill="#a7f3d0">• 高斯模糊虚化答案</text>
        <text x="8" y="48" class="tab-d" fill="#a7f3d0">• 单手点击瞬间揭晓</text>
        <text x="8" y="60" class="tab-d" fill="#a7f3d0">• 碎片默写神器</text>
      </g>
      <g transform="translate(12, 176)">
        <rect width="144" height="52" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#34d399" class="feat-tag">📐 印刷级 KaTeX</text>
        <text x="8" y="32" class="tab-d">离线稳健数学降级</text>
        <text x="8" y="44" class="tab-d">无网环境符号正常</text>
      </g>
      <g transform="translate(12, 238)">
        <text x="72" y="28" text-anchor="middle" fill="#94a3b8" font-size="10.5">睡前与通勤利器</text>
        <text x="72" y="46" text-anchor="middle" fill="#10b981" font-size="11" font-weight="700">拒绝“假看懂”</text>
      </g>
    </g>

    <!-- 页签 3: 薄弱 (Radar) -->
    <g transform="translate(356, 0)">
      <rect width="168" height="310" rx="10" fill="url(#tabGrad1)" stroke="#f59e0b" stroke-width="1.2" filter="url(#shadow3)" />
      <rect x="12" y="12" width="144" height="28" rx="6" fill="#d97706" />
      <text x="84" y="31" text-anchor="middle" fill="#fff" class="tab-t">🎯 薄弱 (Radar)</text>

      <g transform="translate(12, 52)">
        <rect width="144" height="42" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#fbbf24" class="feat-tag">📈 掌握度阶梯雷达</text>
        <text x="8" y="32" class="tab-d">章节量化直暴洼地</text>
      </g>
      <g transform="translate(12, 102)">
        <rect width="144" height="52" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#fbbf24" class="feat-tag">🔄 FSRS 记忆周期</text>
        <text x="8" y="32" class="tab-d">1/3/7/15/28天衰减</text>
        <text x="8" y="44" class="tab-d">自适应调节复测间隔</text>
      </g>
      <g transform="translate(12, 162)">
        <rect width="144" height="52" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#fbbf24" class="feat-tag">⚠️ 错题强制置顶</text>
        <text x="8" y="32" class="tab-d">未掌握标星题库</text>
        <text x="8" y="44" class="tab-d">出库方可消除预警</text>
      </g>
      <g transform="translate(12, 230)">
        <text x="72" y="32" text-anchor="middle" fill="#94a3b8" font-size="10.5">全科阶段性复盘</text>
        <text x="72" y="50" text-anchor="middle" fill="#f59e0b" font-size="11" font-weight="700">靶向消灭错题</text>
      </g>
    </g>

    <!-- 页签 4: 数据 (Analytics) -->
    <g transform="translate(534, 0)">
      <rect width="168" height="310" rx="10" fill="url(#tabGrad1)" stroke="#ec4899" stroke-width="1.2" filter="url(#shadow3)" />
      <rect x="12" y="12" width="144" height="28" rx="6" fill="#db2777" />
      <text x="84" y="31" text-anchor="middle" fill="#fff" class="tab-t">📊 数据 (Data)</text>

      <g transform="translate(12, 52)">
        <rect width="144" height="42" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#f472b6" class="feat-tag">🥧 错因五分类分布</text>
        <text x="8" y="32" class="tab-d">概念/审题/公式/计算/书写</text>
      </g>
      <g transform="translate(12, 102)">
        <rect width="144" height="56" rx="5" fill="#2d1525" stroke="#ec4899" stroke-width="0.8" />
        <text x="8" y="16" fill="#f472b6" class="feat-tag">📉 7日完成率趋势</text>
        <text x="8" y="32" class="tab-d">原生 SVG 平滑曲线</text>
        <text x="8" y="46" class="tab-d">感知连续攻坚律动</text>
      </g>
      <g transform="translate(12, 166)">
        <rect width="144" height="48" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#f472b6" class="feat-tag">🌿 防疲劳监控</text>
        <text x="8" y="32" class="tab-d">连续2天&lt;60%告警</text>
        <text x="8" y="44" class="tab-d">一键 ky relieve 减负</text>
      </g>
      <g transform="translate(12, 230)">
        <text x="72" y="32" text-anchor="middle" fill="#94a3b8" font-size="10.5">理性实证主义</text>
        <text x="72" y="50" text-anchor="middle" fill="#ec4899" font-size="11" font-weight="700">用数据击碎焦虑</text>
      </g>
    </g>

    <!-- 页签 5: 图谱 (Map) -->
    <g transform="translate(712, 0)">
      <rect width="168" height="310" rx="10" fill="url(#tabGrad1)" stroke="#8b5cf6" stroke-width="1.2" filter="url(#shadow3)" />
      <rect x="12" y="12" width="144" height="28" rx="6" fill="#7c3aed" />
      <text x="84" y="31" text-anchor="middle" fill="#fff" class="tab-t">🗺️ 图谱 (Map)</text>

      <g transform="translate(12, 52)">
        <rect width="144" height="42" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#c084fc" class="feat-tag">🌳 官方大纲知识树</text>
        <text x="8" y="32" class="tab-d">全科章节知识全景分层</text>
      </g>
      <g transform="translate(12, 102)">
        <rect width="144" height="56" rx="5" fill="#1f1538" stroke="#8b5cf6" stroke-width="0.8" />
        <text x="8" y="16" fill="#c084fc" class="feat-tag">🏷️ A/B/C/D 四维分级</text>
        <text x="8" y="32" class="tab-d">A熟练 ｜ B巩固</text>
        <text x="8" y="46" class="tab-d">C生疏 ｜ D盲区死角</text>
      </g>
      <g transform="translate(12, 166)">
        <rect width="144" height="48" rx="5" fill="#1e293b" />
        <text x="8" y="16" fill="#c084fc" class="feat-tag">⚡ 一键变式联动</text>
        <text x="8" y="32" class="tab-d">点击盲区考点</text>
        <text x="8" y="44" class="tab-d">终端秒级唤醒变式演练</text>
      </g>
      <g transform="translate(12, 230)">
        <text x="72" y="32" text-anchor="middle" fill="#94a3b8" font-size="10.5">初试无死角保障</text>
        <text x="72" y="50" text-anchor="middle" fill="#8b5cf6" font-size="11" font-weight="700">考点掌握率 100%</text>
      </g>
    </g>
  </g>

  <!-- ══════════════ 底部全端生态说明 ══════════════ -->
  <g transform="translate(40, 420)">
    <rect width="880" height="105" rx="10" fill="#0f172a" stroke="#334155" stroke-width="1.2" filter="url(#shadow3)" />
    
    <g transform="translate(30, 18)">
      <rect width="250" height="70" rx="6" fill="#1e293b" stroke="#38bdf8" stroke-width="1" />
      <text x="15" y="24" fill="#38bdf8" font-size="12" font-weight="700">📱 手机端 PWA 沉浸式应用</text>
      <text x="15" y="44" class="tab-d">Safari/Chrome “添加到主屏幕”</text>
      <text x="15" y="60" class="tab-d">全屏无缝运行，与原生 App 体验一致</text>
    </g>

    <g transform="translate(315, 18)">
      <rect width="250" height="70" rx="6" fill="#1e293b" stroke="#10b981" stroke-width="1" />
      <text x="15" y="24" fill="#34d399" font-size="12" font-weight="700">🌐 印刷级 Web 伴侣 (live.html)</text>
      <text x="15" y="44" class="tab-d">终端与浏览器双端毫秒级同步</text>
      <text x="15" y="60" class="tab-d">动态 KaTeX 渲染矩阵/积分/长难句</text>
    </g>

    <g transform="translate(600, 18)">
      <rect width="250" height="70" rx="6" fill="#1e293b" stroke="#f59e0b" stroke-width="1" />
      <text x="15" y="24" fill="#fbbf24" font-size="12" font-weight="700">🛡️ 隐私脱敏与快照保护</text>
      <text x="15" y="44" class="tab-d">本地完整模式 vs GitHub 脱敏模式</text>
      <text x="15" y="60" class="tab-d">KY_SNAPSHOT_OPT_IN=1 护航数据安全</text>
    </g>
  </g>
</svg>"""

f1 = ASSETS_DIR / "intelligence_architecture.svg"
f2 = ASSETS_DIR / "school_comparator_matrix.svg"
f3 = ASSETS_DIR / "dashboard_5tabs_architecture.svg"

f1.write_text(SVG1, encoding="utf-8")
f2.write_text(SVG2, encoding="utf-8")
f3.write_text(SVG3, encoding="utf-8")

print(f"Generated SVG 1: {f1.name} ({f1.stat().st_size} bytes)")
print(f"Generated SVG 2: {f2.name} ({f2.stat().st_size} bytes)")
print(f"Generated SVG 3: {f3.name} ({f3.stat().st_size} bytes)")
