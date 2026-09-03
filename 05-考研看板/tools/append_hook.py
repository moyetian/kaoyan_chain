# -*- coding: utf-8 -*-
"""给四科 AGENTS.md 追加「收工更新看板」协议（幂等，重复运行无害）"""
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SEC = """

---

## ★ 收工协议：更新手机看板

**每次完成「交作业」批改并写回全部状态文件之后（即输出归档回执之前），必须执行最后一步：**

在终端运行：

```
C:\\Users\\<username>\\Desktop\\考研看板\\auto-update.bat
```

它会重新生成手机看板并推送到 GitHub，约 1 分钟后学生手机上即可看到最新的今日任务、必背清单与薄弱点。

**约束：**
- 只在**状态文件已全部写回之后**运行，否则看板抓到的是旧数据
- 脚本是幂等的：无变更时会自己跳过，重复运行无害
- 若输出 `push failed`，属正常情况（离线或未配远程），**不必重试，也不要因此中断归档流程**
- 运行完在归档回执里加一行：`- 手机看板：已更新`
"""

TARGETS = [
    r"01-数学\AGENTS.md",
    r"02-英语\AGENTS.md",
    r"03-思想政治理论\AGENTS.md",
    r"04-专业课\[你的目标院校]\学习进度\AGENTS.md",
]

for t in TARGETS:
    p = pathlib.Path(t)
    if not p.exists():
        print("[MISS]  " + t)
        continue
    txt = p.read_text(encoding="utf-8")
    if "auto-update.bat" in txt:
        print("[SKIP]  " + p.parent.name + "  (已存在)")
        continue
    p.write_text(txt.rstrip() + SEC, encoding="utf-8")
    print("[OK]    " + p.parent.name)
