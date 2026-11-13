"""Conservative repairs for time scope in a model-proposed (unconfirmed) plan."""
import re
from datetime import date
from .dialogue import next_month

MONTH = re.compile(r"(?:(?P<year>[1-9]\d{3})年)?(?P<month>1[0-2]|0?[1-9])月")
# Accept only plain aggregate wording. Extra dimensions, rankings, day ranges and
# other qualifiers leave a residue and therefore stay on the ordinary validation path.
FILLER = re.compile(
    r"按(?:照)?(?:你|系统)?(?:已展示|已定义|默认|当前|既定|上述|这个|该)?的?口径|"
    r"帮我|请问|查询|统计|计算|展示|查看|对比|比较|请|查|合计|一共|总计|总额|"
    r"总共|是多少|多少|为多少|怎么样|环比|增长率|变化率|并|分别|和|与|到|至|及|的|是|"
    r"[\s，,。？?、：:]"
)

def reconcile_time_scope(question, decision, context, registry):
    """Return evidence codes; only adjust a concrete plan, never an open question."""
    if decision.action != "plan" or decision.questions:
        return []
    changes = decision.changes
    previous = {} if decision.reset else context["conditions"]
    metric = changes.metric_id or previous.get("metric_id")
    if metric not in {"M01", "M02", "M08"}:
        return []
    words = question
    # Remove actual registered metric names before checking for a simple request.
    allowed_metrics = {metric, "M01"} if metric == "M08" else {metric}
    for item in sorted(registry["metrics"], key=lambda x: len(x["name"]), reverse=True):
        if item["id"] in allowed_metrics:
            words = words.replace(item["name"], "")
    matches = list(MONTH.finditer(words))
    if FILLER.sub("", MONTH.sub("", words)) or not matches:
        return []
    if metric != "M08":
        if (len(matches) == 1 and changes.group_by in {"date","month"}
                and previous.get("group_by") is None):
            changes.group_by = None
            return ["period_without_breakdown"]
        return []
    if "环比" not in question or len(matches) != 2 or not matches[0]["year"]:
        return []
    if previous.get("group_by") is not None or changes.group_by not in {None, "month"}:
        return []
    first = date(int(matches[0]["year"]), int(matches[0]["month"]), 1)
    second = date(int(matches[1]["year"] or first.year), int(matches[1]["month"]), 1)
    if first.year >= 9999 or second.year >= 9999 or next_month(first) != second:
        return []
    if changes.start not in (first, second) or changes.end_exclusive != next_month(second):
        return []
    if changes.start == second and changes.group_by is None:
        return []
    changes.start = second
    changes.group_by = None
    return ["month_over_month_target"]

