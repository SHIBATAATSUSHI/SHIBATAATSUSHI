"""測定結果の集計と出力。

端末で眺める用のテキスト、記録として残す用の Markdown と JSON を作る。
数字はすべて実測。価格が未設定のモデルは費用欄が 0 になるので、
そこは「安い」ではなく「測れていない」と読むこと。
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field

from .runner import TaskOutcome


def display_width(text: str) -> int:
    """端末上での表示幅。全角は2桁として数える。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int, align: str = "<") -> str:
    """表示幅を揃えて詰める。日本語混じりの表がずれないようにする。"""
    space = max(0, width - display_width(text))
    if align == ">":
        return " " * space + text
    return text + " " * space


def row(cells: list[tuple[str, int, str]]) -> str:
    """1行分のセルを組み立てる。"""
    return " ".join(pad(text, width, align) for text, width, align in cells)


@dataclass
class ModelSummary:
    """1モデル分の集計。"""

    model_key: str
    model_label: str
    provider: str
    runs: int = 0
    passed: int = 0
    errors: int = 0
    elapsed: float = 0.0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost: float = 0.0
    cost_parts: dict[str, float] = field(default_factory=dict)
    tool_calls: int = 0
    priced: bool = True

    @property
    def pass_rate(self) -> float:
        return self.passed / self.runs if self.runs else 0.0

    @property
    def avg_elapsed(self) -> float:
        return self.elapsed / self.runs if self.runs else 0.0

    @property
    def cost_per_pass(self) -> float | None:
        """成功1件あたりの費用。1件も通らなければ None。"""
        if not self.passed or not self.priced:
            return None
        return self.cost / self.passed

    def to_dict(self) -> dict:
        return {
            "model_key": self.model_key,
            "model_label": self.model_label,
            "provider": self.provider,
            "runs": self.runs,
            "passed": self.passed,
            "errors": self.errors,
            "pass_rate": round(self.pass_rate, 3),
            "total_elapsed_sec": round(self.elapsed, 2),
            "avg_elapsed_sec": round(self.avg_elapsed, 2),
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "tool_calls": self.tool_calls,
            "total_cost_usd": round(self.cost, 6),
            "cost_parts_usd": {k: round(v, 6) for k, v in self.cost_parts.items()},
            "cost_per_pass_usd": (
                round(self.cost_per_pass, 6) if self.cost_per_pass is not None else None
            ),
        }


def summarize(outcomes: list[TaskOutcome]) -> list[ModelSummary]:
    """モデルごとにまとめる。並びは最初に出てきた順。"""
    summaries: dict[str, ModelSummary] = {}
    for outcome in outcomes:
        summary = summaries.get(outcome.model_key)
        if summary is None:
            summary = ModelSummary(
                model_key=outcome.model_key,
                model_label=outcome.model_label,
                provider=outcome.provider,
            )
            summaries[outcome.model_key] = summary
        summary.runs += 1
        summary.passed += 1 if outcome.passed else 0
        summary.errors += 1 if outcome.error else 0
        summary.elapsed += outcome.elapsed
        summary.requests += outcome.requests
        summary.input_tokens += outcome.input_tokens
        summary.output_tokens += outcome.output_tokens
        summary.cache_creation_tokens += outcome.cache_creation_tokens
        summary.cache_read_tokens += outcome.cache_read_tokens
        summary.cost += outcome.cost
        summary.tool_calls += outcome.tool_calls
        for key, value in (outcome.cost_parts or {}).items():
            summary.cost_parts[key] = summary.cost_parts.get(key, 0.0) + value
    for summary in summaries.values():
        summary.priced = summary.cost > 0
    return list(summaries.values())


#: 費用内訳の表示名
_PART_LABELS = (
    ("input", "入力"),
    ("output", "出力"),
    ("cache_write", "キャッシュ書き"),
    ("cache_read", "キャッシュ読み"),
)


def breakdown_line(summary: ModelSummary) -> str:
    """費用の内訳を1行にする。合計だけでは理由が読めないため。"""
    parts = [
        f"{label} {_money(summary.cost_parts.get(key, 0.0))}"
        for key, label in _PART_LABELS
        if summary.cost_parts.get(key, 0.0) > 0
    ]
    tokens = (
        f"(キャッシュ 書き {summary.cache_creation_tokens:,}"
        f" / 読み {summary.cache_read_tokens:,} トークン)"
    )
    return f"{summary.model_key}: " + " + ".join(parts) + f" {tokens}"


def _money(value: float, priced: bool = True) -> str:
    if not priced:
        return "—"
    return f"${value:.4f}"


#: タスク別の表の列(見出し, 幅, 寄せ)
_TASK_COLUMNS = (
    ("モデル", 12, "<"),
    ("タスク", 14, "<"),
    ("結果", 6, "<"),
    ("秒", 7, ">"),
    ("往復", 4, ">"),
    ("ツール", 6, ">"),
    ("費用", 10, ">"),
)

#: モデル別の表の列
_MODEL_COLUMNS = (
    ("モデル", 12, "<"),
    ("成功", 7, ">"),
    ("平均秒", 8, ">"),
    ("入力", 10, ">"),
    ("出力", 10, ">"),
    ("合計費用", 11, ">"),
    ("成功1件", 11, ">"),
)


def _header(columns) -> list[str]:
    line = row([(title, width, align) for title, width, align in columns])
    return [line, "-" * display_width(line)]


def format_text(outcomes: list[TaskOutcome]) -> str:
    """端末向けのテキスト。失敗したタスクは理由も添える。"""
    lines: list[str] = []
    summaries = summarize(outcomes)

    lines.append("=== タスク別 ===")
    lines += _header(_TASK_COLUMNS)
    for outcome in outcomes:
        values = [
            outcome.model_key,
            outcome.task,
            outcome.status,
            f"{outcome.elapsed:.1f}",
            str(outcome.requests),
            str(outcome.tool_calls),
            _money(outcome.cost, outcome.cost > 0),
        ]
        lines.append(
            row(
                [
                    (value, width, align)
                    for value, (_, width, align) in zip(values, _TASK_COLUMNS)
                ]
            )
        )
        if outcome.error:
            lines.append(f"    ! {outcome.error}")
        for check in outcome.failed_checks():
            detail = f" — {check.detail}" if check.detail else ""
            lines.append(f"    ✗ {check.label}{detail}")

    lines.append("")
    lines.append("=== モデル別 ===")
    lines += _header(_MODEL_COLUMNS)
    for summary in summaries:
        per_pass = summary.cost_per_pass
        values = [
            summary.model_key,
            f"{summary.passed}/{summary.runs}",
            f"{summary.avg_elapsed:.1f}",
            f"{summary.input_tokens:,}",
            f"{summary.output_tokens:,}",
            _money(summary.cost, summary.priced),
            _money(per_pass) if per_pass is not None else "—",
        ]
        lines.append(
            row(
                [
                    (value, width, align)
                    for value, (_, width, align) in zip(values, _MODEL_COLUMNS)
                ]
            )
        )

    # 入力と出力のトークン数だけでは合計費用の説明がつかないことが多い。
    # キャッシュ書き込みが効いている場合は内訳を添える。
    with_cache = [
        s for s in summaries if s.priced and (s.cache_creation_tokens or s.cache_read_tokens)
    ]
    if with_cache:
        lines.append("")
        lines.append("=== 費用の内訳 ===")
        for summary in with_cache:
            lines.append(breakdown_line(summary))

    if any(not s.priced for s in summaries):
        lines.append("")
        lines.append("※ 費用が「—」のモデルは価格を未設定にしてある。トークン数だけ見ること。")
    return "\n".join(lines)


def format_markdown(outcomes: list[TaskOutcome], *, title: str = "Shibata Code ベンチ結果") -> str:
    """記録用の Markdown。"""
    summaries = summarize(outcomes)
    lines = [f"# {title}", ""]

    lines.append("## モデル別")
    lines.append("")
    lines.append(
        "| モデル | 提供元 | 成功 | 平均秒 | 往復 | ツール | 入力 | 出力 |"
        " キャッシュ書き | キャッシュ読み | 合計費用 | 成功1件あたり |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in summaries:
        per_pass = s.cost_per_pass
        lines.append(
            f"| {s.model_label} | {s.provider} | {s.passed}/{s.runs} |"
            f" {s.avg_elapsed:.1f} | {s.requests} | {s.tool_calls} |"
            f" {s.input_tokens:,} | {s.output_tokens:,} |"
            f" {s.cache_creation_tokens:,} | {s.cache_read_tokens:,} |"
            f" {_money(s.cost, s.priced)} |"
            f" {_money(per_pass) if per_pass is not None else '—'} |"
        )

    if any(s.priced and s.cost_parts for s in summaries):
        lines.append("")
        lines.append("### 費用の内訳")
        lines.append("")
        for s in summaries:
            if s.priced and s.cost_parts:
                lines.append(f"- {breakdown_line(s)}")

    lines.append("")
    lines.append("## タスク別")
    lines.append("")
    lines.append("| モデル | タスク | 結果 | 秒 | 往復 | ツール | 費用 | 備考 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for o in outcomes:
        notes = []
        if o.error:
            notes.append(o.error)
        notes += [c.label for c in o.failed_checks()]
        note = "; ".join(notes).replace("|", "\\|") or "-"
        lines.append(
            f"| {o.model_key} | {o.task} | {o.status} | {o.elapsed:.1f} |"
            f" {o.requests} | {o.tool_calls} | {_money(o.cost, o.cost > 0)} | {note} |"
        )

    lines.append("")
    return "\n".join(lines)


def to_json(outcomes: list[TaskOutcome], *, meta: dict | None = None) -> str:
    """あとで差分を取れるように JSON で吐く。"""
    payload = {
        "meta": meta or {},
        "summary": [s.to_dict() for s in summarize(outcomes)],
        "runs": [o.to_dict() for o in outcomes],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
