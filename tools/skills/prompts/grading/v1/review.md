请按评分要点为下列【{{subject_name}}】作答打分。

评分视角：{{perspective}}

【题目】
{{question}}

【评分要点】
{{rubric_json}}

【学员作答】
{{student_answer}}

评分规则：
- 逐条判定：full(完全命中) / partial(部分命中) / none(未命中)
- partial 最多得该要点的 50%
- 结论正确但推导跳步严重：结论要点可给分，过程要点记 none
- 空白、完全无关或明确放弃（如"不会"）→ total 记 0
- 不得因字迹/篇幅给分或扣分；书写问题单独记入 mistake_type

严格输出 JSON：
{"total":7.5,"rubric_hits":[{"id":1,"hit":"full","evidence":"引用学员原文中的依据"}],
 "mistake_type":"概念漏洞","confidence":0.85,"reason":"一句话说明扣分依据"}

mistake_type 只能取：{{mistake_types}}
