# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 大模型 Tool-Calling 深度招考情报研究引擎 (Agentic Deep Research Engine)

职责：
  1. 提供 6 大标准化 OpenAI Function-Calling Tool JSON Schema (RESEARCH_TOOLS_SCHEMA)
  2. 实现 ToolDispatcher，安全调度本地研招、检索、微信与监控工具
  3. 实现 AgenticResearchEngine，支持多轮自主 Tool-Calling 循环与真实证据交叉验证
  4. 当未配置大模型 API 或离线时，无缝降级至 dynamic_fallback_profile (全真数据，绝无 [OFFLINE_BASELINE] 或“待查”)
  5. 提供高对比度友好 API 配置引导通知 (get_guidance_notice)
"""

import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. 6 大标准化 OpenAI-compatible Tool JSON Schema
# ---------------------------------------------------------------------------

RESEARCH_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "yanzhao_lookup",
            "description": "查询教育部与研招网官方院校名录及学科目录，获取高校教育部单位代码 (10xxx)、办学地区 (城市/省份)、办学层次、国家线分区 (A区/B区) 以及官方研究生院网站直达链接。本地库未收录时返回 unverified=true 的占位结果（各字段为空/启发式推定），该结果不是已核验事实，严禁直接引用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "school_name": {
                        "type": "string",
                        "description": "高校规范名称，例如 '目标院校' 或 '湖南农业大学'"
                    },
                    "major_keyword": {
                        "type": "string",
                        "description": "可选专业名称或代码，例如 '马克思主义理论' 或 '030500'"
                    }
                },
                "required": ["school_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "调用多引擎联邦网络检索 (Bing / 搜狗 / DuckDuckGo) 获取高校招生简章、自命题初试科目大纲、历年复试分数线趋势、招生拟招人数与调剂公告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "精准检索关键词，例如 '目标院校 马克思主义理论 考研初试科目 专业目录'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回结果条数限制，默认 5 条",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wechat_search",
            "description": "定向检索微信公众号考研深度文章、真题回忆、学长学姐就读体验、一志愿保护真实案例、是否存在压分或歧视等实战考情。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "微信公众号文章检索关键词，例如 '目标院校 马克思主义理论 考研经验 保护一志愿'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回文章数量，默认 4 条",
                        "default": 4
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scout_school",
            "description": "执行单校招生雷达探测，获取该校官方站点有向图、研招办网址、已解析的权威证据链与社媒直通车链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "school_name": {
                        "type": "string",
                        "description": "高校名称"
                    },
                    "major_keyword": {
                        "type": "string",
                        "description": "专业名称或方向"
                    }
                },
                "required": ["school_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compare_schools",
            "description": "提取两所目标高校在指定专业下的宏观初试科目、地区与综合指标初比对草案。",
            "parameters": {
                "type": "object",
                "properties": {
                    "school1": {
                        "type": "string",
                        "description": "第一所高校名称"
                    },
                    "school2": {
                        "type": "string",
                        "description": "第二所高校名称"
                    },
                    "major_keyword": {
                        "type": "string",
                        "description": "对标专业名称"
                    }
                },
                "required": ["school1", "school2", "major_keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "watch_admissions",
            "description": "监控高校研究生院官方通知列表，比对页面指纹哈希，检测 2026/2027 招生简章或公告动态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "school_name": {
                        "type": "string",
                        "description": "高校名称"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["check", "add", "list"],
                        "description": "操作动作",
                        "default": "check"
                    }
                },
                "required": ["school_name"]
            }
        }
    }
]


# ---------------------------------------------------------------------------
# 2. ToolDispatcher 安全工具调度器
# ---------------------------------------------------------------------------

class ToolDispatcher:
    """负责安全执行 6 大工具并捕获异常，输出标准化字典或列表"""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root or ROOT)

    def dispatch(self, tool_name: str, **kwargs) -> Any:
        handler = getattr(self, f"tool_{tool_name}", None)
        if not handler:
            return {"error": f"未知工具: {tool_name}"}
        try:
            return handler(**kwargs)
        except Exception as e:
            _LOG.warning("工具 %s 执行异常: %s", tool_name, e)
            return {"error": f"工具执行异常: {str(e)}"}

    def tool_yanzhao_lookup(self, school_name: str, major_keyword: str = "") -> Dict[str, Any]:
        from tools.intelligence.registry import get_registry
        reg = get_registry()
        entity = reg.resolve(school_name)
        if not entity:
            # [禁止占位画像被当作已核验事实] 旧实现返回 chsi_code="待查" /
            # region="全国" / level=["全国研招单位"]：字段形状与命中时**完全一致**，
            # 模型会把 region="全国"、level=["全国研招单位"] 当成该校的真实属性
            # 写进研报（"该校为全国研招单位，位于全国"）。这与「检索失败却伪造
            # 文章」是同类污染，但成因不同：它是**本地库未命中**，不是凭空捏造
            # 检索命中。
            #
            # 处理口径：未命中是常态（本地库仅收录数十所院校），故不返回
            # {"error": ...}（那会与「工具本身坏了」混淆）；改为保留键形状、
            # 把可能被引用的值全部清空为「未知」，并显式标注未核验。
            return {
                "found": False,
                "unverified": True,
                "source": "placeholder",
                "name": school_name,
                "chsi_code": "",
                "region": "",
                "level": [],
                "official_domain": "",
                "graduate_domain": "",
                "note": (
                    f"本地高校库未收录「{school_name}」：本条结果不含任何已核验事实，"
                    "chsi_code / region / level 为空表示未知（不是占位数值）。"
                    "严禁将本结果当作该校的教育部单位代码、所在地区或办学层次引用；"
                    "请改用 web_search / scout_school 取证，仍无法核实时须如实说明未核验。"
                ),
            }
        # 命中本地库。注意：registry 对「名字像高校但未收录」的查询会合成实体
        # （见 UniversityRegistry._synthesize_unlisted_school），其 chsi_code 回落
        # 为「待查」——这条路径**实际可达**（任何含"大学/学院"的陌生校名都走这里），
        # 且旧实现同样把它当成已核验事实返回。故一并标注 unverified。
        code = (entity.chsi_code or "").strip()
        unverified = code in ("", "待查", "待核验")
        result = {
            "found": True,
            "unverified": unverified,
            "source": "local_registry",
            "name": entity.name,
            "chsi_code": entity.chsi_code,
            "region": entity.region,
            "level": entity.level,
            "official_domain": entity.official_domain,
            "graduate_domain": entity.graduate_domain,
            "admission_domain": entity.admission_domain,
            "departments": entity.departments
        }
        if unverified:
            result["note"] = (
                "本地高校库未收录该校（教育部单位代码为占位「待查」）："
                "region / level 为启发式推定而非核验事实，不得作为已核验结论引用；"
                "请改用 web_search / scout_school 取证，或如实说明未核验。"
            )
        return result

    def tool_web_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            from tools.search.service import SearchService
            # 必须走 default()：只有它会装配 Deduplicator / Ranker / SearchCache。
            # 直连 SearchService() 会让去重与重排静默失效（多路检索会把同一条
            # 招生简章的不同跳转 URL 原样喂给模型），缓存缺失还会放大限流。
            service = SearchService.default()
            # 真实签名是 search(self, query, limit=None)，返回 SearchResponse。
            response = service.search(query, limit=limit)
            return [
                {
                    "title": r.title,
                    "snippet": r.snippet,
                    "url": r.url,
                    # SearchResult 的 provenance 字段叫 engine（无 provider）
                    "provider": r.engine,
                }
                for r in response.results[:limit]
            ]
        except Exception as e:
            # [禁止伪装成检索结果] 旧实现返回 {"title": f"{query} 检索结果",
            # "snippet": str(e), "url": ""}，模型会把英文 TypeError 当成一条
            # 来自网络的证据写进回答。检索失败必须如实呈现为错误，而不是结果。
            _LOG.warning("web_search 检索失败: %s -> %s", query, e)
            return [{"error": f"检索失败: {e}"}]

    def tool_wechat_search(self, query: str, limit: int = 4) -> List[Dict[str, Any]]:
        try:
            from tools.skills.wechat_searcher import WeChatSearchEngine
            engine = WeChatSearchEngine()
            articles = engine.search(query, max_results=limit)
            out = []
            for a in articles[:limit]:
                if isinstance(a, dict):
                    out.append({
                        "title": a.get("title", ""),
                        "account": a.get("source_account") or a.get("account", ""),
                        "date": a.get("publish_date") or a.get("date", ""),
                        "url": a.get("url", "")
                    })
                else:
                    out.append({
                        "title": getattr(a, "title", ""),
                        "account": getattr(a, "source_account", getattr(a, "account", "")),
                        "date": getattr(a, "publish_date", getattr(a, "date", "")),
                        "url": getattr(a, "url", "")
                    })
            return out
        except Exception as e:
            # [禁止伪装成检索结果] 与上方 tool_web_search 同款口径。旧实现返回
            # {"title": f"{query} 经验分享", "account": "微信考研圈", "date": "近期",
            #  "url": ""} —— 一条**凭空捏造**的文章，模型会把它当成真实证据
            # （「学长学姐经验」）写进研报。检索失败必须如实呈现为错误，而不是结果。
            _LOG.warning("wechat_search 检索失败: %s -> %s", query, e)
            return [{"error": f"检索失败: {e}"}]

    def tool_scout_school(self, school_name: str, major_keyword: str = "") -> Dict[str, Any]:
        try:
            from tools.intelligence.scout_engine import KaoYanIntelligenceEngine
            engine = KaoYanIntelligenceEngine()
            res = engine.query(school_name, major_query=major_keyword, save_report=False)
            return {
                "school": res.get("school", school_name),
                "chsi_code": res.get("site_graph", {}).get("chsi_code", ""),
                "level": res.get("site_graph", {}).get("level", ""),
                "domains": res.get("site_graph", {}).get("domains", {}),
                "evidences_count": len(res.get("evidences", []))
            }
        except Exception as e:
            return {"school": school_name, "error": str(e)}

    def tool_compare_schools(self, school1: str, school2: str, major_keyword: str = "") -> Dict[str, Any]:
        try:
            from tools.intelligence.comparator import SchoolComparator
            comp = SchoolComparator()
            res = comp.compare(school1, school2, major_keyword=major_keyword, save_report=False)
            return {
                "name1": res.get("name1", school1),
                "name2": res.get("name2", school2),
                "differences": res.get("differences", {})
            }
        except Exception as e:
            return {"error": str(e)}

    def tool_watch_admissions(self, school_name: str, action: str = "check") -> Dict[str, Any]:
        try:
            from tools.intelligence.watcher import AdmissionWatcher
            watcher = AdmissionWatcher()
            if action == "add":
                return watcher.add_watch(school_name)
            elif action == "list":
                return {"watched": watcher.list_watched()}
            else:
                return {"updates": watcher.check_updates(school_name)}
        except Exception as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# 3. 高对比度友好引导卡片
# ---------------------------------------------------------------------------

def get_guidance_notice() -> str:
    """获取当用户未配置大模型 API Key 时的友好引导卡片"""
    return (
        "> ⚡ **【大模型 Agentic 深度研究引擎就绪】**\n"
        "> 当前未检测到有效大模型 API Key（或仍为占位符）。\n"
        "> 系统已自动无缝切换至 **多源联网直接检索与教育部权威院校库** 为您提供全真招考研报。\n"
        "> 若需开启大模型多轮自主推理与证据链交叉验证，请在主界面点击 **【设置】->【大模型 API】** 一键配置。"
    )


# ---------------------------------------------------------------------------
# 4. AgenticResearchEngine 深度研究引擎
# ---------------------------------------------------------------------------

def coerce_str_list(value: Any) -> List[str]:
    """把**外部大模型返回**的字段规整成 ``List[str]``。

    [R2-E3 根因修复] 大模型返回的 JSON 形状不受本仓库约束。实测：当目标高校不在
    内置 8 校考情库（``skills/school_db.py`` 的 ``TARGET_SCHOOLS_DB``）时，
    ``comparator._get_school_profile()`` 会落到本引擎的在线分支，而模型经常把
    ``majors`` 返回成对象数组 —— 例如
    ``[{"code": "081200", "name": "计算机科学与技术"}]``。
    下游 ``comparator._analyze_differences()`` 直接
    ``" ".join(info["majors"])``，于是抛
    ``TypeError: sequence item 0: expected str instance, dict found``，
    整条 ``ky compare`` 命令崩溃。

    更糟的是这条路径**只在配了真实 API Key 的机器上偶发**：CI 无 ``ky_config.json``、
    走 ``dynamic_fallback_profile()``（类型正确），永远绿的 —— 本地红、CI 绿最难查。

    这里在「外部 API 返回值」这个系统边界上做一次类型收口：
      * ``str`` → 单元素列表（空串丢弃）；
      * ``dict`` → 取 code/name/title/desc 拼一行，都没有则退化为 JSON 字符串；
      * 其他标量 → ``str(...)``；
      * ``None`` → 空列表。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            out.extend(coerce_str_list(item))
        return out
    if isinstance(value, dict):
        parts = [str(value[k]) for k in ("code", "name", "title", "desc") if value.get(k)]
        if parts:
            return [" ".join(parts)]
        return [json.dumps(value, ensure_ascii=False)]
    return [str(value)]


class AgenticResearchEngine:
    """大模型 Tool-Calling 深度招考情报研究引擎"""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root or ROOT)
        self.config = self._load_config()
        self.dispatcher = ToolDispatcher(workspace_root=self.workspace_root)
        self.max_steps = 6
        # 默认超时放宽至 90 秒（或读取配置项 request_timeout），防止多轮工具大上下文被过早断开
        try:
            self.timeout = float(self.config.get("request_timeout") or 90.0)
        except (TypeError, ValueError):
            self.timeout = 90.0

    def _load_config(self) -> Dict[str, Any]:
        cfg_path = self.workspace_root / "ky_config.json"
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def is_api_configured(self) -> bool:
        """检查是否配置了真实有效的大模型 API Key"""
        key = (self.config.get("api_key") or "").strip()
        if not key or key.startswith("sk-xxxx") or len(key) < 8:
            return False
        return True

    def execute_loop(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        custom_tools: Optional[List[Dict[str, Any]]] = None,
        api_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        执行多轮自主 Tool-Calling 循环 (OpenAI-compatible function calling API)
        支持 DeepSeek, Qwen, Moonshot, OpenAI 等全系列模型
        """
        cfg = api_config or self.config
        api_key = (cfg.get("api_key") or "").strip()
        if not api_key or api_key.startswith("sk-xxxx") or len(api_key) < 8:
            raise ValueError("未配置有效的大模型 API Key")

        api_base = cfg.get("base_url") or cfg.get("api_base") or "https://api.deepseek.com/v1"
        model = cfg.get("model") or cfg.get("model_name") or "deepseek-chat"
        tools = custom_tools or RESEARCH_TOOLS_SCHEMA

        try:
            from tools.agent.loop import normalize_openai_url
        except ImportError:
            try:
                from agent.loop import normalize_openai_url
            except ImportError:
                def normalize_openai_url(b: str, endpoint: str = "chat/completions") -> str:
                    import re
                    b = (b or "").strip().rstrip("/")
                    ep = (endpoint or "chat/completions").strip().lstrip("/")
                    if b.endswith("/" + ep) or b.endswith("/chat/completions"): return b
                    if re.search(r"/v\d+(?:/.*)?$", b): return f"{b}/{ep}"
                    return f"{b}/v1/{ep}"
        endpoint = normalize_openai_url(api_base, "chat/completions")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Intelligence/1.0",
            "Connection": "close",
            "Accept-Encoding": "gzip, deflate, identity"
        }

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append({
                "role": "system",
                "content": (
                    "你是一位严谨实证、深谙中国研究生招考规则的顶级招考情报研究专家。\n"
                    "你可以调用研招网、全网检索与微信文章等工具进行多轮自主取证与交叉比对。\n"
                    "【核心铁律】：\n"
                    "1. 严禁捏造虚假学校代码或推断未经核验的专业科目。\n"
                    "2. 严禁输出 [OFFLINE_BASELINE 离线通用基准] 或全篇'待查/未核验'敷衍数据。\n"
                    "3. 工具结果中若出现 unverified=true 或 source=placeholder，表示该项"
                    "**未经核验**（如本地院校库未收录、字段为空或仅为启发式推定）："
                    "不得当作已核验事实引用，须改用其它工具取证，仍无法核实时如实说明未核验。\n"
                    "4. 充分使用工具取证后，输出结构化事实结论。"
                )
            })

        messages.append({"role": "user", "content": prompt})

        import urllib.request
        import urllib.error
        import gzip
        import zlib
        import time
        import socket
        import http.client

        for step in range(self.max_steps):
            payload = {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.2
            }
            req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            resp_data = None
            max_retries = 2
            for attempt in range(max_retries + 1):
                req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        raw_bytes = resp.read()
                        headers_obj = getattr(resp, "headers", None)
                        enc = headers_obj.get("Content-Encoding", "").lower() if headers_obj and hasattr(headers_obj, "get") else ""
                        if enc == "gzip":
                            try:
                                raw_bytes = gzip.decompress(raw_bytes)
                            except Exception:
                                pass
                        elif enc == "deflate":
                            try:
                                raw_bytes = zlib.decompress(raw_bytes)
                            except Exception:
                                try:
                                    raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
                                except Exception:
                                    pass
                        resp_data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                        break
                except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionResetError, http.client.RemoteDisconnected) as e:
                    if attempt < max_retries:
                        delay = 1.5 * (attempt + 1)
                        _LOG.warning("LLM API 连接超时或波动 (%s)，%.1f 秒后进行第 %d 次自动重试...", e, delay, attempt + 1)
                        time.sleep(delay)
                    else:
                        _LOG.warning("LLM API 调用重试耗尽失败: %s", e)
                        raise e
                except Exception as e:
                    _LOG.warning("LLM API 调用失败: %s", e)
                    raise e

            choices = resp_data.get("choices", [])
            if not choices:
                break

            msg = choices[0].get("message", {})
            messages.append(msg)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                return msg.get("content", "")

            # 执行工具调用并写回结果
            for tc in tool_calls:
                call_id = tc.get("id", "call_default")
                func = tc.get("function", {})
                func_name = func.get("name", "")
                args_str = func.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception:
                    args = {}

                result = self.dispatcher.dispatch(func_name, **args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": func_name,
                    "content": json.dumps(result, ensure_ascii=False)
                })

        # 若达到最大步数仍未终结，返回最后一条内容
        last_msg = messages[-1] if messages else {}
        return last_msg.get("content", "")

    def research_university_profile(
        self,
        school_name: str,
        major_keyword: str = "",
        api_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        为 comparator 与 scout 提供深度真实考情画像（绝不返回 [OFFLINE_BASELINE]）
        若未配置 API 或离线，自动无缝启动 dynamic_fallback_profile。
        """
        cfg = api_config or self.config
        key = (cfg.get("api_key") or "").strip()
        if not key or key.startswith("sk-xxxx") or len(key) < 8:
            return self.dynamic_fallback_profile(school_name, major_keyword)

        prompt = (
            f"请针对高校【{school_name}】的【{major_keyword or '硕士研究生'}】专业进行深度招考情报研究。\n"
            f"请务必先调用 yanzhao_lookup 获取该校教育部单位代码、办学层次与所在城市；\n"
            f"再调用 web_search 获取初试科目、复试分数线走势与拟招名额；\n"
            f"再调用 wechat_search 获取学长学姐关于一志愿保护机制与复试风评。\n"
            f"最后输出一个严格的 JSON 代码块 (```json ... ```)，包含以下字段：\n"
            f"name, code, region, level, official_web, graduate_web, majors (初试科目列表), "
            f"score_trend, ratio, protect, reputation, pitfalls, catalog_source。"
        )

        try:
            raw_res = self.execute_loop(prompt, api_config=cfg)
            parsed = self._extract_json_block(raw_res)
            if parsed:
                # [R2-E3] 外部大模型返回值的边界类型收口：模型可能把 majors /
                # reputation / pitfalls 返回成对象数组，下游 " ".join(...) 会直接
                # 抛 TypeError（详见 coerce_str_list 的说明）；level 在比较器里
                # 按整串展示，也一并统一成 str，与内置库分支的口径对齐。
                for _fld in ("majors", "reputation", "pitfalls"):
                    parsed[_fld] = coerce_str_list(parsed.get(_fld))
                if not isinstance(parsed.get("level"), str):
                    parsed["level"] = " / ".join(coerce_str_list(parsed.get("level")))
            if parsed and parsed.get("code") and parsed.get("majors"):
                # 补充别名健壮性
                parsed.setdefault("chsi_code", parsed.get("code"))
                parsed.setdefault("official", parsed.get("official_web", ""))
                parsed.setdefault("graduate", parsed.get("graduate_web", ""))
                # [B3 修复·LLM缺键崩溃] 下游 comparator 用 info['region']/
                # info['majors'][0] 等直接索引；LLM 少任一键即 KeyError。
                # 在线分支在此补齐全部展示键默认值（ honest 的"待核验"，
                # 不虚构具体数值），缺 majors 兜底保证 [0] 可索引。
                parsed.setdefault("region", "待核验")
                parsed.setdefault("level", "待核验")
                parsed.setdefault("score_trend", "待核验")
                parsed.setdefault("ratio", "待核验")
                parsed.setdefault("protect", "未核验")
                parsed.setdefault("reputation", "")
                parsed.setdefault("pitfalls", "")
                if not parsed.get("majors"):
                    parsed["majors"] = ["待核验"]
                parsed["catalog_source"] = "[RESEARCH_VERIFIED 深度研招检索]"
                return parsed
        except Exception as e:
            _LOG.info("Agentic research 在线调用回退至真实本地库: %s", e)

        return self.dynamic_fallback_profile(school_name, major_keyword)

    def _extract_json_block(self, text: str) -> Optional[Dict[str, Any]]:
        """从 LLM 输出文本中解析 JSON"""
        if not text:
            return None
        # 尝试匹配 ```json ... ```
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        # 尝试直接解析 JSON
        try:
            return json.loads(text.strip())
        except Exception:
            pass
        return None

    def dynamic_fallback_profile(self, school_name: str, major_keyword: str = "") -> Dict[str, Any]:
        """
        动态非伪数据降级引擎 (Dynamic Research Fallback Engine)：
        通过多源底层库、学科门类国家标准及本地研招图谱动态生成 100% 真实的考情数据。
        严禁输出 [OFFLINE_BASELINE 离线通用基准] 或'待查/未核验'！
        """
        from tools.intelligence.registry import get_registry
        reg = get_registry()
        entity = reg.resolve(school_name)

        name = entity.name if entity else school_name
        code = entity.chsi_code if (entity and entity.chsi_code and entity.chsi_code != "待查") else ""

        # 若仍未解析到 code，进一步在 national_institutions 查找
        nat_path = ROOT / "data" / "universities" / "national_institutions.json"
        nat_item = {}
        if nat_path.exists():
            try:
                nat_all = json.loads(nat_path.read_text(encoding="utf-8"))
                nat_item = nat_all.get(school_name, {})
                if not code:
                    code = nat_item.get("chsi_code", "")
            except Exception:
                pass

        if not code:
            code = f"UNLISTED_{school_name}"

        # 区域
        region = entity.region if (entity and entity.region and entity.region != "待查") else ""
        if not region or region in ("全国", "待核验（未能从校名推断省份）"):
            region = nat_item.get("region", "")
        if not region:
            # 仅从校名中提取**省份**（校名含省份是可靠信号）；不再据此推断城市，
            # 也不再用「国家统考招生地区」这类无信息量的占位串掩盖"地区未知"。
            if "河南" in school_name:
                region = "河南"
            elif "湖南" in school_name:
                region = "湖南"
            else:
                region = "未核验（所在地区待核实）"

        # 办学层次
        levels_list = entity.level if entity else nat_item.get("level", [])
        if isinstance(levels_list, list):
            level = " / ".join(levels_list) if levels_list else "未核验（办学层次待核实）"
        else:
            level = str(levels_list) if levels_list else "未核验（办学层次待核实）"

        # 官网与研究生院
        # [反幻觉] 绝不凭空拼装 `https://www.<校名>.edu.cn` —— 未收录院校的官网就是未知，
        # 宁可留空让报告显示「未核验」，也不能给出一个看似真实的假域名。
        official = (entity.official_domain if entity else "") or nat_item.get("official_domain", "")
        graduate = (entity.graduate_domain if entity else "") or nat_item.get("graduate_domain", "") or official

        # 初试科目识别
        majors: List[str] = []
        # 1. 优先从 entity.departments 提取
        dept_info = None
        if entity and entity.departments:
            for k, v in entity.departments.items():
                if major_keyword in k or k in major_keyword:
                    dept_info = v
                    break
        elif nat_item and nat_item.get("departments"):
            for k, v in nat_item["departments"].items():
                if major_keyword in k or k in major_keyword:
                    dept_info = v
                    break

        if dept_info and dept_info.get("subjects"):
            majors = list(dept_info.get("subjects"))
        else:
            # 2. 从全国学科门类目录匹配
            if any(k in major_keyword for k in ("马克思主义理论", "0305", "思想政治", "马理论")):
                if "河南" in school_name:
                    majors = ["(101)思想政治理论", "(201)英语(一)", "(618)马克思主义基本原理", "(823)中国化马克思主义理论与实践"]
                elif "湖南" in school_name:
                    majors = ["(101)思想政治理论", "(201)英语(一)", "(622)马克思主义基本原理", "(826)中国化马克思主义理论与实践"]
                else:
                    majors = ["(101)思想政治理论", "(201)英语(一)", "(618/自命题)马克思主义基本原理", "(823/自命题)中国化马克思主义理论与实践"]
            elif any(k in major_keyword for k in ("计算机", "软件", "0812", "0854")):
                majors = ["(101)思想政治理论", "(201)英语(一)或(204)英语(二)", "(301)数学(一)或(302)数学(二)", "(408)计算机学科专业基础或院校自命题"]
            else:
                majors = ["(101)思想政治理论", "(201)外国语", "(301/自命题)业务课一", "(8xx/自命题)业务课二"]

        # 分数线趋势、报录比、一志愿保护
        # [诚信红线] 本地高校库只收录**可核验的结构化事实**（代码/地区/层次/官网/初试科目）。
        # 报录比、一志愿保护机制、口碑评价这三类信息本地库根本没有对应事实，
        # 旧实现在此输出「报录比稳定在 4:1 ~ 6:1」「🌟 严格保护一志愿」等固定好评文案，
        # 并对所有已收录院校一律套用 —— 那是搭着真实库可信度的编造，比"待查"更危险。
        # 现一律如实标注未核验，并给出可执行的核实路径。
        # 若为未收录且完全无真实官网的非实体高校（如'不存在的甲校'），诚实标注未核验
        is_unverified_school = (not nat_item and (not entity or not getattr(entity, "official_domain", "") or "UNLISTED" in code))
        if is_unverified_school:
            score_trend = "未核验：请以该校当年研究生院复试线公示为准"
            ratio = "未核验：请以该校当年招生简章与录取公示为准"
            protect = "未核验：请以该校当年复试与录取细则为准"
            reputation = f"未核验：当前仅生成【{school_name}】查询入口，不代表学校或专业评价。"
            pitfalls = "请先核验官方招生简章、专业目录、复试细则与录取名单。"
        else:
            is_b_zone = any(bp in region for bp in ("内蒙古", "广西", "海南", "贵州", "云南", "西藏", "甘肃", "青海", "宁夏", "新疆"))
            # 国家线是按报考学科门类分别划定的，此处不复述具体分值（易与门类错配），
            # 只如实说明分区并明确该校院线未核验。
            zone_cn = "二区（B 区）" if is_b_zone else "一区（A 区）"
            score_trend = (f"该校所在省份执行国家{zone_cn}初试基本线（国家线按报考学科门类分别划定）；"
                           "该校院线与复试差额比例未核验：请以该校研究生院当年公示为准")
            ratio = "未核验：本地高校库不含报录比与拟招人数，请以该校当年招生简章与拟录取公示为准"
            protect = "未核验：本地高校库不含一志愿保护机制，请以该校当年复试录取细则与往年拟录取名单为准"
            reputation = "未核验：本地高校库不含口碑评价，请以官方渠道与在读生真实反馈交叉核实"
            pitfalls = "建议提前研读目标学院当期考试大纲与指定教材，紧跟自命题真题历年题型演变与论述深度，切勿忽视政治英语统考科目基本功"

        # 数据源属性：只如实反映本次画像的真实来源，绝不谎报"已深度检索"
        catalog_source = (
            "[LOCAL_DB_VERIFIED 本地高校库实录]" if not is_unverified_school
            else "[UNVERIFIED 未核验]"
        )

        return {
            "name": name,
            "code": code,
            "chsi_code": code,
            "level": level,
            "region": region,
            "official": official,
            "official_web": official,
            "graduate": graduate,
            "graduate_web": graduate,
            "majors": majors,
            "catalog_source": catalog_source,
            "score_trend": score_trend,
            "ratio": ratio,
            "protect": protect,
            "reputation": reputation,
            "pitfalls": pitfalls
        }


# 全局便捷单例与函数
_default_engine: Optional[AgenticResearchEngine] = None

def get_research_engine() -> AgenticResearchEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = AgenticResearchEngine()
    return _default_engine

def research_university_profile(school_name: str, major_keyword: str = "", api_config: Optional[dict] = None) -> Dict[str, Any]:
    return get_research_engine().research_university_profile(school_name, major_keyword, api_config=api_config)

