请为下列【{{subject_name}}】题目生成评分要点（rubric）。

要求：
1. 输出 3~6 条**可客观判定**的要点，覆盖：关键定义/定理条件、核心推导步骤、最终结论、常见易错点。
2. 每条给出建议分值，所有要点分值之和必须为 10。
3. 严禁输出模糊要点（如"回答得好""思路清晰"）。

【题目】
{{question}}

【参考答案/采分点】
{{reference}}

严格输出 JSON：
{"rubric":[{"id":1,"point":"要点描述","score":3.0,"must_have":true}],"derived_from":"reference"}
（若上方无标准答案，derived_from 填 "question_only"）
