# -*- coding: utf-8 -*-
"""未收录高校实体合成器（自 registry.py 拆出）。

提供 ``synthesize_unlisted_school``：按校名启发式推导未收录高校的区域、
办学层次与教育部研招直达通道，供 ``UniversityRegistry`` 解析未命中时调用。
"""

import urllib.parse

from .models import UniversityEntity


# 常见双非/地方高校教育部代码与域名索引
KNOWN_REGIONAL = {
    "东莞理工学院": ("11819", "广东东莞", "http://yjs.dgut.edu.cn", "https://www.dgut.edu.cn"),
    "河南农业大学": ("10466", "河南郑州", "https://gra.henau.edu.cn", "https://www.henau.edu.cn"),
    "湖南农业大学": ("10537", "湖南长沙", "https://yjsy.hunau.edu.cn", "https://www.hunau.edu.cn"),
    "河南科技大学": ("10464", "河南洛阳", "https://yjsc.haust.edu.cn", "https://www.haust.edu.cn"),
    "河南理工大学": ("10460", "河南焦作", "http://admissions.hpu.edu.cn", "https://www.hpu.edu.cn"),
    "河南工业大学": ("10463", "河南郑州", "https://yjs.haut.edu.cn", "https://www.haut.edu.cn"),
    "桂林电子科技大学": ("10595", "广西桂林", "https://www.guet.edu.cn/yjsy", "https://www.guet.edu.cn"),
    "杭州电子科技大学": ("10336", "浙江杭州", "https://grs.hdu.edu.cn", "https://www.hdu.edu.cn"),
    "重庆邮电大学": ("10617", "重庆", "http://yjs.cqupt.edu.cn", "https://www.cqupt.edu.cn"),
    "西安邮电大学": ("10709", "陕西西安", "http://gr.xupt.edu.cn", "https://www.xupt.edu.cn"),
    "长春理工大学": ("10186", "吉林长春", "http://yzb.cust.edu.cn", "https://www.cust.edu.cn"),
    "齐鲁工业大学": ("10431", "山东济南", "https://yjs.qlu.edu.cn", "https://www.qlu.edu.cn"),
    "燕山大学": ("10216", "河北秦皇岛", "https://ysuyzb.ysu.edu.cn", "https://www.ysu.edu.cn"),
    "江苏大学": ("10299", "江苏镇江", "https://yz.ujs.edu.cn", "https://www.ujs.edu.cn"),
    "南京邮电大学": ("10293", "江苏南京", "http://yzb.njupt.edu.cn", "https://www.njupt.edu.cn"),
    "广东工业大学": ("11845", "广东广州", "http://yjs.gdut.edu.cn", "https://www.gdut.edu.cn"),
    "深圳大学": ("10590", "广东深圳", "https://yz.szu.edu.cn", "https://www.szu.edu.cn"),
    "浙江工业大学": ("10337", "浙江杭州", "http://www.yz.zjut.edu.cn", "https://www.zjut.edu.cn"),
    "太原科技大学": ("10109", "山西太原", "https://yjs.tyust.edu.cn", "https://www.tyust.edu.cn"),
    "中北大学": ("10110", "山西太原", "http://grs.nuc.edu.cn", "https://www.nuc.edu.cn"),
    "常州大学": ("10292", "江苏常州", "http://yzb.cczu.edu.cn", "https://www.cczu.edu.cn"),
    "青岛科技大学": ("10426", "山东青岛", "https://grad.qust.edu.cn", "https://www.qust.edu.cn"),
    "三峡大学": ("11075", "湖北宜昌", "http://sxdxpos.ctgu.edu.cn", "https://www.ctgu.edu.cn"),
    "长江大学": ("10489", "湖北荆州", "http://gs.yangtzeu.edu.cn", "https://www.yangtzeu.edu.cn"),
    "长沙理工大学": ("10536", "湖南长沙", "https://yjsy.csust.edu.cn", "https://www.csust.edu.cn")
}

# 城市 → 省市区名（校名含城市关键字时推断区域）
CITY_MAP = {
    "郑州": "河南郑州", "长沙": "湖南长沙",
    "东莞": "广东东莞", "洛阳": "河南洛阳", "焦作": "河南焦作", "开封": "河南开封", "新乡": "河南新乡",
    "保定": "河北保定", "秦皇岛": "河北秦皇岛", "徐州": "江苏徐州", "苏州": "江苏苏州", "无锡": "江苏无锡",
    "常州": "江苏常州", "南通": "江苏南通", "镇江": "江苏镇江", "温州": "浙江温州", "宁波": "浙江宁波",
    "杭州": "浙江杭州", "芜湖": "安徽芜湖", "蚌埠": "安徽蚌埠", "赣州": "江西赣州", "九江": "江西九江",
    "绵阳": "四川绵阳", "泸州": "四川泸州", "湘潭": "湖南湘潭", "株洲": "湖南株洲", "衡阳": "湖南衡阳",
    "宜昌": "湖北宜昌", "荆州": "湖北荆州", "襄阳": "湖北襄阳", "大连": "辽宁大连", "鞍山": "辽宁鞍山",
    "吉林": "吉林", "齐齐哈尔": "黑龙江齐齐哈尔", "桂林": "广西桂林", "柳州": "广西柳州",
    "延安": "陕西延安", "汉中": "陕西汉中", "青岛": "山东青岛", "烟台": "山东烟台", "威海": "山东威海"
}

# 省名前缀（校名以省名开头时推断区域）
_PROVINCES = [
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江",
    "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州",
    "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古", "广西", "西藏", "宁夏", "新疆"
]

# 国家线 B 区省份（判定 A 区 / B 区）
_B_ZONE_PROVINCES = ("内蒙古", "广西", "海南", "贵州", "云南", "西藏", "甘肃", "青海", "宁夏", "新疆")


def _infer_region(school_name: str) -> str:
    """校名 → 区域推断：城市关键字优先，其次省名前缀，未识别回落「全国」。"""
    for city_key, reg_val in CITY_MAP.items():
        if city_key in school_name:
            return reg_val
    for prov in _PROVINCES:
        if school_name.startswith(prov):
            return prov
    return "全国"


def synthesize_unlisted_school(school_name: str) -> UniversityEntity:
    """启发式推导未收录高校的区域、办学层次与教育部研招直达通道。

    [性能] 旧实现在此处重新读取 national_institutions.json 做精确匹配，
    但该文件的全部条目在 ``load()`` 阶段已进入 ``_name_map``，而 ``resolve()``
    的步骤 2 已做过精确名称匹配——此处再读盘属于纯冗余，且在全国库扩容到
    近两千所后每次未命中查询都要重新解析一个数兆字节的 JSON。现直接移除。
    """
    chsi_code = "待查"
    region = "全国"
    grad_domain = ""
    official_domain = ""

    # 1. 命中知名双非对照表
    if school_name in KNOWN_REGIONAL:
        code, reg, grad, off = KNOWN_REGIONAL[school_name]
        chsi_code = code
        region = reg
        grad_domain = grad
        official_domain = off
    else:
        # 2. 地理区域启发式提取
        region = _infer_region(school_name)

        # 官方通道降级：直达研招网该校专页
        encoded = urllib.parse.quote(school_name)
        official_domain = f"https://yz.chsi.com.cn/sch/search.do?ssdm=&yjsy=&xxmc={encoded}"
        grad_domain = official_domain

    # 3. 判定国家线分区 (A区 vs B区)
    is_b_zone = any(bp in region for bp in _B_ZONE_PROVINCES)
    zone_label = "国家二区线高校 (B区，享受降分照顾)" if is_b_zone else "国家一区线高校 (A区)"

    # [P2 修复·未收录院校误导] 此前对未收录高校无差别打出「省属重点公办高校」，
    # 且区域未识别时回落为「全国」，看起来像已核实的官方标签，实则纯属猜测。
    # 现明确标注「未收录·待核验」，并把未知区域标注为「待核验」，避免误导考生。
    region_unverified = (region in ("全国", "", None))
    region_display = "待核验（未能从校名推断省份）" if region_unverified else region
    if school_name in KNOWN_REGIONAL:
        levels = ["省属重点公办高校（本地对照表推定）", "硕士研究生培养单位", zone_label]
    else:
        levels = ["未收录院校 · 办学层次待核验（请以研招网/官网为准）", "硕士研究生培养单位", zone_label]

    return UniversityEntity(
        chsi_code=chsi_code,
        name=school_name,
        aliases=[school_name],
        level=levels,
        region=region_display,
        official_domain=official_domain,
        graduate_domain=grad_domain,
        admission_domain=grad_domain,
        departments={},
        province="" if region_unverified else region,
        zone="B区" if is_b_zone else "A区",
    )
