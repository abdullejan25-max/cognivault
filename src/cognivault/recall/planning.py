"""Conservative local planning; inputs must be real user messages supplied by a Host."""
from dataclasses import dataclass
from math import isfinite
import re
from typing import Mapping

DOMAINS = ("memory", "history", "study")


@dataclass(frozen=True)
class RecallRequest:
    session_id: str
    request_id: str
    message: str
    recent_user_messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecallPolicy:
    allowed_domains: tuple[str, ...] = DOMAINS
    deadline_seconds: float = 3.0

    def __post_init__(self):
        if (not isinstance(self.allowed_domains, tuple)
                or any(d not in DOMAINS for d in self.allowed_domains)
                or len(set(self.allowed_domains)) != len(self.allowed_domains)):
            raise ValueError("Invalid allowed domains")
        if (type(self.deadline_seconds) not in (int, float)
                or not isfinite(self.deadline_seconds) or not 0 < self.deadline_seconds <= 30):
            raise ValueError("Deadline must be positive and at most thirty seconds")


@dataclass(frozen=True)
class RecallPlan:
    queries: Mapping[str, str]
    rewrites: tuple[Mapping[str, str], ...] = ()
    per_backend_limit: int = 3


def validate_plan(plan: RecallPlan, policy: RecallPolicy) -> RecallPlan:
    """Copy a Host plan once so later mutation cannot expand its authority/budget."""
    if (not isinstance(plan, RecallPlan) or type(plan.per_backend_limit) is not int
            or not 1 <= plan.per_backend_limit <= 3 or len(plan.rewrites) > 2):
        raise ValueError("Invalid plan budget")
    attempts = []
    for queries in (plan.queries, *plan.rewrites):
        if not isinstance(queries, Mapping) or set(queries) - set(policy.allowed_domains):
            raise ValueError("Invalid plan domains")
        if any(type(q) is not str or not q.strip() or len(q) > 200
               or any(ord(c) < 32 for c in q) for q in queries.values()):
            raise ValueError("Invalid plan query")
        attempts.append(dict(queries))
    if any(set(q) != set(attempts[0]) for q in attempts[1:]):
        raise ValueError("Rewrites cannot expand domains")
    return RecallPlan(attempts[0], tuple(attempts[1:]), plan.per_backend_limit)


def _window(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    # Include start, middle, and end instead of losing a long question's conclusion.
    third = budget // 3
    middle = len(text) // 2
    return text[:third] + text[middle:middle + third] + text[-(budget - 2 * third):]


def local_plan(request: RecallRequest, policy: RecallPolicy) -> RecallPlan:
    if (type(request.message) is not str or type(request.recent_user_messages) is not tuple
            or any(type(m) is not str for m in request.recent_user_messages)):
        raise ValueError("Only user-message strings are accepted")
    recent = request.recent_user_messages[-2:]
    recent_budget = min(400, sum(min(len(m), 200) for m in recent))
    current = _window(request.message, 2000 - recent_budget - len(recent))
    context = " ".join([current, *(_window(m, 200) for m in recent)])[:2000]
    reference = re.search(
        r"之前|上次|曾经|记得|历史|偏好|目标|决定|选择|我的|那个|那句|那段|当时|昨天|前天|上周|上个月|去年|"
        r"我.{0,12}(?:写|发|说|提到|记录|保存|做)的|"
        r"my\b|our\b|previous|last time|remember|yesterday|last week",
        current, re.I,
    )
    expression = re.sub(r"^(?:请帮我|帮我|请)?\s*(?:计算|算一下|calculate)?\s*", "", current, flags=re.I)
    independent_math = re.fullmatch(r"[\d\s.+*/×÷()=%^−-]+[?？。]?", expression)
    translation = re.match(r"^(?:请帮我|帮我|请)?\s*(?:翻译|translate\b)\s*(.*)$", current, re.I)
    payload = translation.group(1).strip() if translation else ""
    if payload.startswith((":", "：")):
        payload = payload[1:].strip()
    # Absence of a known reference word does not prove that text is supplied.
    # A colon is only a separator. Require a fully quoted literal or short ASCII
    # text; ambiguous Chinese payloads conservatively retain personal recall.
    quote_pairs = {'"': '"', "'": "'", "“": "”", "「": "」"}
    marked_payload = len(payload) > 2 and quote_pairs.get(payload[:1]) == payload[-1:]
    ascii_literal = (0 < len(payload) <= 100
                     and re.fullmatch(r"[A-Za-z0-9\s'\".,!?;-]+", payload)
                     and not re.search(r"\b(?:what|my|our|we|the|this|that|it|last|earlier|previous|saved|wrote|discussed|messages)\b", payload, re.I))
    independent_translation = len(current) <= 300 and translation and (marked_payload or ascii_literal)
    if not reference and (independent_math or independent_translation):
        return RecallPlan({})
    learning = re.search(r"复习|学习|错题|考试|成绩|试卷|作业|study|review|exam", current, re.I)
    # Recent context resolves short user continuations; it never turns an explicit
    # unrelated task into a personal one.
    continuation = re.search(r"^(那|这个|然后|继续|还有|为什么|what about|continue)", current, re.I)
    if continuation:
        learning = learning or re.search(r"复习|学习|错题|考试", context)
    domains = DOMAINS if learning else ("memory", "history")
    domains = tuple(d for d in domains if d in policy.allowed_domains)
    quoted = re.findall(r'[“"「]([^”"」]{1,80})[”"」]', context)[:3]
    subjects = [term for term in ("物理", "化学", "数学", "英语", "语文", "错题", "考试", "成绩") if term in context]
    # Keep an explicitly named English subject as a literal query. Word
    # boundaries avoid inventing "physics" when the user names astrophysics.
    subjects.extend(term for term in ("physics", "chemistry", "math", "mathematics", "english", "chinese")
                    if re.search(rf"\b{term}\b", context, re.I))
    predicates = [term for term in ("偏好", "目标", "选择", "决定") if term in context]
    if quoted:
        choices = {d: quoted + predicates + subjects for d in domains}
    elif "选择" in predicates or "选科" in context:
        choices = {"memory": subjects + ["选择", "决定", "选科"],
                   "history": (["选科", "选择"] + subjects) if subjects else ["选择", "选科", "决定"]}
        choices["study"] = subjects + ["学习", "复习"]
    elif "偏好" in predicates:
        choices = {d: ["偏好", "喜欢", "prefer"] for d in domains}
        if "study" in domains:
            choices["study"] = subjects + ["复习", "学习"]
    elif learning:
        choices = {"memory": subjects + ["目标", "goal", "学习"],
                   "history": subjects + ["近期学习", "学习", "复习"],
                   "study": subjects + ["复习", "学习", "近期学习"]}
    elif predicates:
        choices = {d: predicates + ["goal", "计划"] for d in domains}
    else:
        # A single short literal topic: the Gateway does substring matching, not
        # token AND/OR search. Never append filler phrases to a failed query.
        stripped = re.sub(r"我|之前|上次|讨论的|怎么|什么|继续|接下来|推进|请问|那个", " ", current)
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,24}|[\u4e00-\u9fff]{2,6}", stripped)
        # Recognize a named topic after an explicit past-mention frame. Fixed
        # CJK chunks can split the topic and mix both fragments with question words.
        mention = re.fullmatch(
            r"(?:请问[，, ]*)?我(?:之前|上次|曾经)有?"
            r"(?:提到过|提到(?!过)|提过|说过|记录过|保存过|写过)"
            r"(?:哪(?:一)?[个种项份类门])?"
            r"(?!哪|什么|怎么|如何|为何|为什么|是否|有没有|多少)"
            r"([\u4e00-\u9fff]{2,80}?)(?:吗|么)?[?？。]?",
            current.strip(),
        )
        candidates = ([mention.group(1)] if mention else []) + tokens[:3]
        choices = {d: candidates or ["个人记录", "目标", "偏好"] for d in domains}
    choices = {d: list(dict.fromkeys(re.sub(r"\s+", " ", q).strip() for q in choices[d]))[:3]
               for d in domains}
    attempts = []
    for index in range(3):
        queries = {d: values[min(index, len(values) - 1)] for d, values in choices.items()}
        if queries not in attempts:
            attempts.append(queries)
    return validate_plan(RecallPlan(attempts[0], tuple(attempts[1:])), policy)
