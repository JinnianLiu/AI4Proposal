#!/usr/bin/env python3
"""Offline self-check for the experiment-1 harness. No API key, no network.

Covers what must hold before any money is spent: the no-blueprint prompts carry
no blueprint references, the frozen tasks and the unified prompt render cleanly,
normalization only deletes what it should, void detection catches the failures
that produced the invalid flash41 runs, and the collectors read run records.

  uv run python tests/test_experiments.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ai4proposal import writer_prompts as WP  # noqa: E402

import collect  # noqa: E402
import judge  # noqa: E402
import run_c1  # noqa: E402
from common import load_task, task_ids  # noqa: E402
from normalize import normalize, over_limit_sections, void_reasons  # noqa: E402
from prompts_render import render  # noqa: E402

BLUEPRINT_TOKENS = ["${thesis}", "${deliverables}", "${key_methods}", "${novelty_angles}", "${facts}",
                    "主线", "台账", "成果清单"]


def check(label, cond):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    return bool(cond)


def main() -> int:
    ok = True

    print("\n== C2 prompts (no blueprint) ==")
    triggers = [(kw[0], "章节") for kw, _ in WP.MODIFIERS] + [("无触发", "其他")]
    leftovers = []
    for req, name in triggers:
        s, u = WP.writer_prompts(req, name, blueprint=False)
        leftovers += [t for t in BLUEPRINT_TOKENS if t in s + u]
    ok &= check("no blueprint variable or reference survives in any modifier combination", not leftovers)
    s_full, u_full = WP.writer_prompts("考核指标", "考核指标", blueprint=True)
    ok &= check("full-system prompts are unchanged",
                s_full == WP.GENERAL_RULES + "\n\n" + WP.WRITER_SYSTEM + WP.modifiers_for("考核指标", "考核指标")
                and u_full == WP.WRITER_USER)
    ok &= check("the consistency rule replaces the ledger rule", WP.CONSISTENCY_RULE in WP.GENERAL_RULES_NO_BLUEPRINT)
    _, u_nb = WP.writer_prompts("x", "章节", blueprint=False)
    ok &= check("C2 writer still sees the title, background and challenges the blueprint came from",
                all(v in u_nb for v in ("${title}", "${background}", "${challenges}")))
    ok &= check("word limits carry the document's unit",
                (WP.word_limit_text("en", 550), WP.word_limit_text("zh", 800), WP.word_limit_text("zh", None))
                == ("550 words", "800字", "不限"))

    print("\n== frozen tasks and the unified C1/C4 prompt ==")
    ids = task_ids()
    ok &= check("12 frozen tasks", len(ids) == 12)
    ok &= check("every task carries a structure with required elements",
                all(all(s.get("required") for s in load_task(t)["structure"]["core_sections"]) for t in ids))
    bad = []
    for t in ids:
        try:
            system, user, c1 = render(load_task(t))
            names = [s["name"] for s in load_task(t)["structure"]["core_sections"]]
            if not all(n in user for n in names) or "proposal.md" not in c1:
                bad.append(t)
        except Exception as exc:          # noqa: BLE001
            bad.append(f"{t}: {exc}")
    ok &= check("every task renders with all chapters and no unfilled placeholder", not bad)
    task = load_task("task_008")
    system, user, _ = render(task)
    pipeline_vars = WP.task_vars(task, task["structure"])
    ok &= check("baseline sees the same requirement text the pipeline does",
                pipeline_vars["requirements"] in user and pipeline_vars["constraints"] in user)
    ok &= check("English limit rendered in words", "550 words" in user)

    print("\n== normalization (delete only) ==")
    t = {"language": "zh", "structure": {"core_sections": [{"name": "课题目标"}, {"name": "考核指标", "word_limit": 10}]}}
    raw = ("好的，以下是申请书：\n\n# 标题\n\n## 课题目标\n\n正文一。[figure: 图 || 描述]\n\n"
           "## 考核指标\n\n十一个字的正文内容啊啊\n\n---\n\n以上是完整的申请书，如需修改请告诉我。\n")
    text, rep = normalize(raw, t)
    ok &= check("leading chat removed", text.startswith("# 标题"))
    ok &= check("trailing chat and rule removed", "以上是" not in text and not text.rstrip().endswith("---"))
    ok &= check("figure marker removed", "[figure:" not in text and "正文一。" in text)
    ok &= check("removals are recorded", len(rep["removed"]) == 3 and rep["removed_chars"] > 0)
    body = "# 标题\n\n## 课题目标\n\n最后一段是正文，不能删。\n"
    ok &= check("a real closing paragraph is kept", normalize(body, t)[0] == body)
    fenced, _ = normalize("```markdown\n# 标题\n\n## 课题目标\n\nx\n```", t)
    ok &= check("outer code fence stripped", fenced.startswith("# 标题") and "```" not in fenced)
    ok &= check("missing chapter reported, not voided",
                normalize("# 标题\n\n## 课题目标\n\nx\n", t)[1]["missing_sections"] == ["考核指标"]
                and not void_reasons("# 标题\n\n## 课题目标\n\nx\n"))
    ok &= check("over-limit chapter measured", [o["section"] for o in over_limit_sections(text, t)] == ["考核指标"])

    print("\n== void detection (the flash41 failure modes) ==")
    ok &= check("pipeline [待补充] fallback voids the run", bool(void_reasons("## 课题目标\n\n[待补充]")))
    ok &= check("Step 0 fallback voids the run", bool(void_reasons("x", {"blueprint_fallback": True})))
    ok &= check("empty output voids the run", bool(void_reasons("  ")))
    ok &= check("judge result with an error is incomplete",
                not judge.is_complete({"errors": ["402 Insufficient Balance"], "scores": {}}))
    from ai4proposal.evaluation import WEIGHTS
    ok &= check("judge result with all seven dimensions and no error is complete",
                judge.is_complete({"errors": [], "scores": {k: 7.0 for k in WEIGHTS}}))

    print("\n== collectors and C1 config ==")
    with tempfile.TemporaryDirectory() as tmp:
        runs = Path(tmp)
        rd = runs / "gen" / "C4" / "task_001" / "r1"
        rd.mkdir(parents=True)
        (rd / "run.json").write_text(json.dumps({
            "run_id": "C4__task_001__r1", "task_id": "task_001", "condition": "C4", "status": "valid",
            "char_count": 123, "finish_reason": "length", "normalization": {"removed_chars": 5}}), encoding="utf-8")
        jd = runs / "judge" / "gemini" / "C4" / "task_001"
        jd.mkdir(parents=True)
        (jd / "r1_j1.json").write_text(json.dumps({
            "run_id": "C4__task_001__r1", "task_id": "task_001", "condition": "C4", "judge": "gemini",
            "judge_rep": 1, "status": "valid",
            "result": {"scores": {"clarity": 8}, "overall_100": 71.0, "verdict": "revise_resubmit"}}),
            encoding="utf-8")
        collect.RUNS, run_c1.RUNS = runs, runs
        rr, jr = collect.run_rows(), collect.judge_rows()
        ok &= check("run record collected", len(rr) == 1 and rr[0][0] == "C4__task_001__r1" and "length" in rr[0])
        ok &= check("judge record collected", len(jr) == 1 and 71.0 in jr[0] and "revise_resubmit" in jr[0])
        home = run_c1._cx_home(runs / "codex_home", "统一 system 提示词", "## 工作方式\n\n写入 proposal.md。")
        try:
            import tomllib
            cfg = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            ok &= check("Codex config parses, search off, sandboxed, DeepSeek via /responses",
                        cfg["web_search"] == "disabled" and cfg["sandbox_mode"] == "workspace-write"
                        and cfg["model_providers"]["deepseek"]["wire_api"] == "responses"
                        and "proposal.md" in cfg["developer_instructions"]
                        and "统一 system 提示词" in cfg["developer_instructions"]
                        and "model_instructions_file" not in cfg)
        except ModuleNotFoundError:
            print("  [skip] tomllib unavailable (Python < 3.11)")
    ok &= check("task sheet lists every frozen task", len(collect.task_rows()) == 12)

    print("\n" + ("PASS" if ok else "FAIL") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
