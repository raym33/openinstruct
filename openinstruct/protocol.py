from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


class ProtocolError(ValueError):
    pass


@dataclass
class Action:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelReply:
    summary: str = ""
    actions: List[Action] = field(default_factory=list)
    final: str = ""


def build_system_prompt(workdir: Path, tool_manifest: str) -> str:
    return f"""
You are OpenInstruct, a local coding agent.
You work only inside this workspace root: {workdir}

Available tools:
{tool_manifest}

Rules:
- Always answer with exactly one JSON object inside a ```json fenced block.
- Use this schema:
  {{
    "summary": "brief next-step summary",
    "actions": [
      {{"tool": "tool_name", "args": {{"key": "value"}}}}
    ],
    "final": "final answer for the user"
  }}
- If you need tools, put them in "actions" and leave "final" as an empty string.
- If the task is complete, return no actions and write the answer in "final".
- Do not invent tools.
- Prefer small, verifiable steps.
- Paths must be relative to the workspace root unless the user explicitly asks otherwise.
""".strip()


def extract_json_candidate(text: str) -> str:
    fenced = re.findall(r"```json\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced[0].strip()

    generic_fenced = re.findall(r"```\s*(.*?)```", text, flags=re.DOTALL)
    if generic_fenced:
        return generic_fenced[0].strip()

    start = text.find("{")
    if start == -1:
        raise ProtocolError("No JSON object found in model response.")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ProtocolError("Incomplete JSON object in model response.")


def parse_model_response(text: str) -> ModelReply:
    try:
        payload = json.loads(extract_json_candidate(text))
    except json.JSONDecodeError as exc:
        raise ProtocolError("Invalid JSON returned by model.") from exc

    if not isinstance(payload, dict):
        raise ProtocolError("Model response must be a JSON object.")

    summary = str(payload.get("summary") or payload.get("thought") or "").strip()
    final = str(payload.get("final") or payload.get("message") or "").strip()

    raw_actions = payload.get("actions")
    if raw_actions is None and "action" in payload:
        raw_actions = [payload["action"]]
    if raw_actions is None:
        raw_actions = []
    if isinstance(raw_actions, dict):
        raw_actions = [raw_actions]
    if not isinstance(raw_actions, list):
        raise ProtocolError("'actions' must be a list.")

    actions: List[Action] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            raise ProtocolError("Each action must be an object.")
        tool = item.get("tool")
        args = item.get("args") or {}
        if not isinstance(tool, str) or not tool.strip():
            raise ProtocolError("Each action must include a string 'tool'.")
        if not isinstance(args, dict):
            raise ProtocolError("Each action 'args' must be an object.")
        actions.append(Action(tool=tool.strip(), args=args))

    return ModelReply(summary=summary, actions=actions, final=final)


def render_tool_results(results: List[Dict[str, Any]]) -> str:
    payload = {"tool_results": results}
    return (
        "Tool results are ready. Continue using the same JSON protocol.\n"
        "```json\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=True)}\n"
        "```"
    )
