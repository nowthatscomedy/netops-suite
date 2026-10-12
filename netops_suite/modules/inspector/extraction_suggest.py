"""Turn a value the user selected in command output into an extraction rule.

Every suggestion is checked by running it against the sample with the same
semantics the inspector runtime uses, so a suggestion is only offered when it
really extracts the selected value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

_MIN_KEYWORD_CHARS = 2
_LABEL_CHARS = re.compile(r"[A-Za-z가-힣]")


class SelectionError(ValueError):
    """The selection cannot become a column value."""


@dataclass(slots=True)
class ExtractionSuggestion:
    method: str
    description: str
    value: str
    column_name: str
    line_number: int = 1
    start_field: int = 1
    end_field: int = 1
    keyword: str = ""
    regex: str = ""
    # Rules tied to a line or field position break when output shifts.
    position_based: bool = False

    def as_column(self, command: str, name: str | None = None) -> dict[str, Any]:
        return {
            "name": name if name is not None else self.column_name,
            "command": command,
            "method": self.method,
            "line_number": self.line_number,
            "start_field": self.start_field,
            "end_field": self.end_field,
            "keyword": self.keyword,
            "regex": self.regex,
            "python_parser": "",
        }


@dataclass(slots=True)
class _Selection:
    value: str
    line_index: int
    line: str
    start: int
    end: int
    lines: list[str] = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return self.line[: self.start]

    @property
    def suffix(self) -> str:
        return self.line[self.end :]


def extract_value(column: dict[str, Any], sample: str) -> str:
    """Extract like the runtime does (first match, whitespace fields)."""
    method = column.get("method", "split_fields")
    lines = sample.splitlines()
    line_number = int(column.get("line_number", 1) or 1)
    if method == "split_fields":
        if not 1 <= line_number <= len(lines):
            return ""
        fields = lines[line_number - 1].split()
        start = int(column.get("start_field", 1) or 1)
        end = int(column.get("end_field", start) or start)
        if not 1 <= start <= len(fields):
            return ""
        return " ".join(fields[start - 1 : min(max(end, start), len(fields))])
    if method == "keyword_after":
        keyword = str(column.get("keyword", "")).strip()
        if not keyword:
            return ""
        for line in lines:
            if keyword in line:
                return line.split(keyword, 1)[1].strip(" :\t")
        return ""
    if method == "line_text":
        if 1 <= line_number <= len(lines):
            return lines[line_number - 1].strip()
        return ""
    if method == "regex":
        pattern = str(column.get("regex", "")).strip()
        if not pattern:
            return ""
        try:
            match = re.search(pattern, sample, flags=re.MULTILINE)
        except re.error:
            return ""
        if match is None:
            return ""
        return match.group(1) if match.lastindex else match.group(0)
    return ""


def _locate(sample: str, start: int, end: int) -> _Selection:
    start, end = sorted((max(0, start), min(len(sample), end)))
    raw = sample[start:end]
    if "\n" in raw.strip("\r\n") or "\r" in raw.strip("\r\n"):
        raise SelectionError("값은 한 줄 안에서 골라 주세요.")
    # Ignore spaces the mouse picked up around the value.
    while start < end and sample[start] in " \t\r\n":
        start += 1
    while end > start and sample[end - 1] in " \t\r\n":
        end -= 1
    if start >= end:
        raise SelectionError("출력에서 Excel에 넣을 값을 마우스로 드래그해 고르세요.")
    line_start = sample.rfind("\n", 0, start) + 1
    line_end = sample.find("\n", end)
    if line_end < 0:
        line_end = len(sample)
    line = sample[line_start:line_end].rstrip("\r")
    lines = sample.splitlines()
    line_index = sample.count("\n", 0, line_start)
    return _Selection(
        value=sample[start:end],
        line_index=line_index,
        line=line,
        start=start - line_start,
        end=end - line_start,
        lines=lines,
    )


def _value_pattern(value: str, next_text: str, stop_char: str = "") -> str:
    if stop_char and not re.search(r"\s", value):
        # The value is glued to the next text ("1419044K/6147K"): stop at it.
        return r"([^\s" + re.escape(stop_char) + r"]+)"
    if re.fullmatch(r"\d+", value):
        return r"(\d+)"
    if re.fullmatch(r"\d+(?:\.\d+)+", value):
        return r"(\d+(?:\.\d+)+)"
    if re.fullmatch(r"\d+(?:\.\d+)?%", value):
        return r"(\d+(?:\.\d+)?%)"
    if not re.search(r"\s", value):
        return r"(\S+)"
    if next_text:
        return r"(.+?)\s*" + re.escape(next_text)
    return r"(.+?)\s*$"


def _loose(text: str) -> str:
    """Literal text where any run of spaces may vary between devices."""
    return r"\s+".join(re.escape(part) for part in text.split())


def _label_from(text: str) -> str:
    label = text.strip().strip(":=#-|[](){}").strip()
    return re.sub(r"\s+", " ", label)[:60]


def _label_phrase(prefix: str) -> str:
    """The words right before the value that read like its label."""
    stripped = prefix.rstrip()
    tokens = list(re.finditer(r"\S+", stripped))
    start = None
    for token in reversed(tokens[-4:]):
        text = token.group(0)
        if (
            not _LABEL_CHARS.search(text)
            or re.search(r"\d", text)
            or text.endswith((".", ",", ";"))
        ):
            break
        start = token.start()
    return stripped[start:] if start is not None else ""


def _anchor_candidates(prefix: str) -> list[str]:
    """Pieces of the text before the value: the label first, then shortest first."""
    stripped = prefix.rstrip()
    starts = [match.start() for match in re.finditer(r"\S+", stripped)]
    candidates = []
    label = _label_phrase(prefix)
    if label:
        candidates.append(label)
    for start in reversed(starts):
        candidate = stripped[start:]
        if (
            candidate not in candidates
            and len(candidate.strip(" :=")) >= _MIN_KEYWORD_CHARS
            and _LABEL_CHARS.search(candidate)
        ):
            candidates.append(candidate)
    return candidates


def suggest_extractions(sample: str, start: int, end: int) -> list[ExtractionSuggestion]:
    """Return verified rules for the selected text, most robust first."""
    selection = _locate(sample, start, end)
    value = selection.value
    suggestions: list[ExtractionSuggestion] = []

    def add(candidate: ExtractionSuggestion) -> None:
        if extract_value(candidate.as_column(""), sample) != value:
            return
        if any(
            (item.method, item.keyword, item.regex, item.line_number, item.start_field)
            == (candidate.method, candidate.keyword, candidate.regex,
                candidate.line_number, candidate.start_field)
            for item in suggestions
        ):
            return
        suggestions.append(candidate)

    prefix, suffix = selection.prefix, selection.suffix
    anchors = _anchor_candidates(prefix)
    label_name = _label_from(_label_phrase(prefix))

    # 1) "value after this label" when the value runs to the end of the line.
    if not suffix.strip():
        for keyword in anchors:
            label = _label_from(keyword)
            add(ExtractionSuggestion(
                method="keyword_after",
                description=f"'{keyword.strip()}' 뒤의 값",
                value=value,
                column_name=label_name or label or "값",
                keyword=keyword.strip(),
            ))

    # 2) Label before the value, with a value shape (number, word, ...).
    next_word = suffix.split()[0] if suffix.split() else ""
    stop_char = suffix[:1] if suffix[:1].strip() else ""
    shape = _value_pattern(value, next_word, stop_char)
    for keyword in anchors:
        anchor = _loose(keyword.strip())
        add(ExtractionSuggestion(
            method="regex",
            description=f"'{keyword.strip()}' 다음에 오는 값",
            value=value,
            column_name=label_name or _label_from(keyword) or "값",
            regex=anchor + r"\s*[:=]?\s*" + shape,
        ))

    # 3) Label after the value at the start of a line ("1234K bytes of memory").
    follow_text = suffix.strip()[:40]
    if follow_text and _LABEL_CHARS.search(follow_text) and not prefix.strip():
        leading = _value_pattern(value, "") if not re.search(r"\s", value) else r"(.+?)"
        add(ExtractionSuggestion(
            method="regex",
            description=f"'{follow_text}' 앞의 값",
            value=value,
            column_name=_label_from(follow_text) or "값",
            regex=r"^\s*" + leading + r"\s*" + _loose(follow_text),
        ))

    # 4) Whole line, or a line/field position as the last resort.
    line_number = selection.line_index + 1
    if value == selection.line.strip():
        add(ExtractionSuggestion(
            method="line_text",
            description=f"{line_number}번째 줄 전체",
            value=value,
            column_name="값",
            line_number=line_number,
            position_based=True,
        ))
    fields = [match for match in re.finditer(r"\S+", selection.line)]
    first = next((i for i, m in enumerate(fields) if m.start() == selection.start), None)
    last = next((i for i, m in enumerate(fields) if m.end() == selection.end), None)
    if first is not None and last is not None and last >= first:
        add(ExtractionSuggestion(
            method="split_fields",
            description=(
                f"{line_number}번째 줄의 {first + 1}번째 값"
                if first == last
                else f"{line_number}번째 줄의 {first + 1}~{last + 1}번째 값"
            ),
            value=value,
            column_name=_label_from(prefix.split()[-1]) if prefix.split() else "값",
            line_number=line_number,
            start_field=first + 1,
            end_field=last + 1,
            position_based=True,
        ))
    if not suggestions:
        raise SelectionError(
            "이 값을 다시 찾을 규칙을 만들지 못했습니다. 값 앞뒤의 글자까지 조금 넓게 골라 보거나 "
            "'추출 규칙 직접 고치기'에서 정규식을 입력하세요."
        )
    return suggestions
