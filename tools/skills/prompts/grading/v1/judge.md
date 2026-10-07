多位阅卷人对同一份【{{subject_name}}】作答给出不一致评分，请裁定。

【题目】
{{question}}

【评分要点】
{{rubric_json}}

【学员作答】
{{student_answer}}

【各阅卷人评分】
{{reviews_json}}

裁定要求：
1. 逐条审视分歧点，**以学员作答文本中的实际证据为准**，不得凭印象给分。
2. 证据不足时倾向**较低分**（宁可让学员复核，不可虚高通过）。
3. 给出你认可的最终分数与逐条命中。

严格输出 JSON：
{"total":6.0,"rubric_hits":[{"id":1,"hit":"full","evidence":"..."}],
 "mistake_type":"概念漏洞","confidence":0.9,"reason":"仲裁依据：..."}

mistake_type 只能取：{{mistake_types}}
