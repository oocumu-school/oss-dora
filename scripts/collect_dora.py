#!/usr/bin/env python3
"""
DORA 4대 지표 수집기
====================

GitHub API(파이썬 표준 라이브러리만 사용)로 다음 데이터를 읽어 주(週) 단위 DORA 지표를 계산한다.
  - 배포 워크플로우(DEPLOY_WORKFLOW)의 실행 기록  -> 배포 빈도, 변경 실패율, MTTR
  - 배포 사이에 들어간 커밋                         -> 변경 리드 타임
  - `incident` 라벨이 붙은 이슈(선택)               -> MTTR 보조 데이터

산출물
  metrics/latest.json      이번 실행의 요약 (JSON 아티팩트로도 업로드)
  metrics/history.json     주별 기록 (최근 HISTORY_WEEKS 주는 매번 다시 계산, 그 이전은 보존)
  docs/dora/dora-chart.png README에 첨부되는 추이 그래프 (matplotlib 설치 시)
  README.md                <!-- DORA:START --> ~ <!-- DORA:END --> 구간을 주간 리포트로 교체

사용법
  python scripts/collect_dora.py            # 실제 수집 (GITHUB_REPOSITORY, GITHUB_TOKEN 필요)
  python scripts/collect_dora.py --demo     # 가짜 데이터로 미리보기 -> demo-output/ 에만 기록
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
MARK_START, MARK_END = "<!-- DORA:START -->", "<!-- DORA:END -->"

# Windows 콘솔(cp949)에서도 한글/이모지 출력이 깨지지 않도록
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hours_between(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / 3600.0, 0.0)


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------
class GitHub:
    def __init__(self, api: str, repo: str, token: str | None):
        self.api, self.repo, self.token = api.rstrip("/"), repo, token

    def _request(self, url: str):
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "dora-metrics-collector")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")), resp.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"GitHub API {e.code} {url}\n{body}") from None

    def get(self, path: str, params: dict | None = None):
        url = f"{self.api}/repos/{self.repo}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._request(url)[0]

    def paginate(self, path: str, params: dict, key: str | None = None, max_pages: int = 10):
        url = f"{self.api}/repos/{self.repo}{path}?" + urllib.parse.urlencode({**params, "per_page": 100})
        items: list = []
        for _ in range(max_pages):
            data, link = self._request(url)
            items.extend(data[key] if key else data)
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
            if not nxt:
                break
            url = nxt.group(1)
        return items

    # --- 지표에 필요한 데이터만 정리해서 돌려준다 ---
    def deploy_runs(self, workflow: str, branch: str, since: datetime) -> list[dict]:
        raw = self.paginate(
            f"/actions/workflows/{urllib.parse.quote(workflow)}/runs",
            {"branch": branch, "status": "completed", "created": f">={since.date().isoformat()}"},
            key="workflow_runs",
        )
        runs = [
            {
                "id": r["id"],
                "sha": r["head_sha"],
                "conclusion": r["conclusion"],
                "finished": parse_ts(r["updated_at"]),
                "url": r["html_url"],
            }
            for r in raw
            if r.get("conclusion") in ("success", "failure")  # cancelled/skipped 은 배포 시도로 보지 않음
        ]
        return sorted(runs, key=lambda r: r["finished"])

    def commit_dates(self, base_sha: str | None, head_sha: str) -> list[datetime]:
        """이번 배포에 새로 포함된 커밋들의 작성 시각."""
        if base_sha is None:
            c = self.get(f"/commits/{head_sha}")
            return [parse_ts(c["commit"]["author"]["date"])]
        if base_sha == head_sha:
            return []  # 같은 커밋 재배포: 새 변경 없음
        data = self.get(f"/compare/{base_sha}...{head_sha}")
        return [parse_ts(c["commit"]["author"]["date"]) for c in data.get("commits", [])]

    def incidents(self, label: str, since: datetime) -> list[dict]:
        raw = self.paginate("/issues", {"labels": label, "state": "all", "since": since.isoformat()}, max_pages=5)
        out = []
        for i in raw:
            if "pull_request" in i:
                continue
            out.append({
                "number": i["number"],
                "created": parse_ts(i["created_at"]),
                "closed": parse_ts(i["closed_at"]) if i.get("closed_at") else None,
            })
        return out


class DemoGitHub:
    """--demo 용 가짜 데이터. 실제 API 를 호출하지 않는다."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self._commits: dict[str, list[datetime]] = {}
        self._runs: list[dict] = []
        now = datetime.now(timezone.utc)
        t = now - timedelta(weeks=12)
        n = 0
        while t < now:
            t += timedelta(hours=self.rng.uniform(6, 40))
            if t >= now:
                break
            n += 1
            sha = f"{n:040x}"
            self._commits[sha] = [t - timedelta(hours=self.rng.uniform(0.5, 30)) for _ in range(self.rng.randint(1, 4))]
            failed = self.rng.random() < 0.18
            self._runs.append({"id": n, "sha": sha, "conclusion": "failure" if failed else "success",
                               "finished": t, "url": "https://github.com/demo/demo/actions"})

    def deploy_runs(self, workflow, branch, since):
        return [r for r in self._runs if r["finished"] >= since]

    def commit_dates(self, base_sha, head_sha):
        if base_sha == head_sha:
            return []
        # 실패한 배포의 커밋은 다음 성공 배포에 포함된다
        ids = [r["sha"] for r in self._runs]
        start = ids.index(base_sha) + 1 if base_sha else ids.index(head_sha)
        dates: list[datetime] = []
        for sha in ids[start: ids.index(head_sha) + 1]:
            dates.extend(self._commits[sha])
        return dates

    def incidents(self, label, since):
        return []


# ---------------------------------------------------------------------------
# 지표 계산
# ---------------------------------------------------------------------------
def week_start(d: datetime) -> datetime:
    d = d.astimezone(KST)
    return (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def collect_samples(gh, cfg: dict, weeks: list[datetime]) -> dict:
    """주 구간과 무관하게 '사건'(배포, 리드타임 표본, 복구 표본)을 시간순으로 모은다."""
    oldest = weeks[0]
    lookback = oldest - timedelta(days=60)  # 첫 주의 기준 배포/진행 중인 장애를 찾기 위한 여유
    runs = gh.deploy_runs(cfg["deploy_workflow"], cfg["branch"], lookback)

    lead_samples: list[tuple[datetime, float]] = []   # (배포 완료 시각, 리드타임 h)
    recoveries: list[tuple[datetime, float, str]] = []  # (복구 시각, 소요 h, 출처)
    prev_success_sha: str | None = None
    first_failure: datetime | None = None

    for run in runs:
        if run["conclusion"] == "success":
            if run["finished"] >= oldest:
                for committed in gh.commit_dates(prev_success_sha, run["sha"]):
                    lead_samples.append((run["finished"], hours_between(committed, run["finished"])))
            if first_failure is not None:
                recoveries.append((run["finished"], hours_between(first_failure, run["finished"]), "deploy"))
                first_failure = None
            prev_success_sha = run["sha"]
        else:  # failure
            if first_failure is None:
                first_failure = run["finished"]

    try:
        for inc in gh.incidents(cfg["incident_label"], lookback):
            if inc["closed"]:
                recoveries.append((inc["closed"], hours_between(inc["created"], inc["closed"]), "incident"))
    except RuntimeError as e:  # 이슈 권한이 없어도 나머지 지표는 계속 계산
        print(f"[경고] incident 이슈 조회 실패 - 배포 기반 MTTR만 사용합니다.\n{e}")

    return {"runs": runs, "lead": lead_samples, "recoveries": recoveries,
            "open_failure_since": first_failure}


def summarize(samples: dict, start: datetime, end: datetime, n_weeks: float) -> dict:
    def inside(t: datetime) -> bool:
        return start <= t < end

    attempts = [r for r in samples["runs"] if inside(r["finished"])]
    success = [r for r in attempts if r["conclusion"] == "success"]
    failed = [r for r in attempts if r["conclusion"] == "failure"]
    lead = [h for t, h in samples["lead"] if inside(t)]
    recov = [h for t, h, _ in samples["recoveries"] if inside(t)]

    return {
        "deployments": len(success),
        "deploy_attempts": len(attempts),
        "failed_deployments": len(failed),
        "deployments_per_week": round(len(success) / n_weeks, 2) if n_weeks else None,
        "lead_time_hours": round(median(lead), 2) if lead else None,
        "lead_time_samples": len(lead),
        "mttr_hours": round(mean(recov), 2) if recov else None,
        "recovery_samples": len(recov),
        "change_failure_rate": round(len(failed) / len(attempts) * 100, 1) if attempts else None,
    }


# 등급 기준: Accelerate State of DevOps 보고서의 구간을 과제용으로 단순화
def grade(metric: str, value) -> str:
    if value is None:
        return "—"
    tiers = {
        # (Elite, High, Medium) 경계
        "deployments_per_week": lambda v: 0 if v >= 7 else 1 if v >= 1 else 2 if v >= 0.25 else 3,
        "lead_time_hours": lambda v: 0 if v < 24 else 1 if v < 24 * 7 else 2 if v < 24 * 30 else 3,
        "mttr_hours": lambda v: 0 if v < 1 else 1 if v < 24 else 2 if v < 24 * 7 else 3,
        "change_failure_rate": lambda v: 0 if v <= 15 else 1 if v <= 30 else 2 if v <= 45 else 3,
    }
    return ["🟢 Elite", "🔵 High", "🟡 Medium", "🔴 Low"][tiers[metric](value)]


DEFINITIONS = {
    "deployment_frequency": "집계 기간 동안 성공한 배포 워크플로우 실행 수 (주당 평균)",
    "lead_time": "커밋 작성 시각 → 그 커밋이 포함된 첫 성공 배포 완료 시각 (중앙값)",
    "mttr": "배포 실패 → 다음 성공 배포까지 걸린 시간 + incident 이슈 생성→종료 시간 (평균)",
    "change_failure_rate": "실패한 배포 실행 수 ÷ 전체 배포 시도 수 (cancelled/skipped 제외)",
}


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
def fmt_hours(h) -> str:
    if h is None:
        return "—"
    if h < 1:
        return f"{h * 60:.0f}분"
    if h < 48:
        return f"{h:.1f}시간"
    return f"{h / 24:.1f}일"


def fmt_pct(v) -> str:
    return "—" if v is None else f"{v:.1f}%"


def render_chart(history: list[dict], path: Path, repo: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[안내] matplotlib 이 없어 그래프 PNG 생성을 건너뜁니다. (pip install matplotlib)")
        return False

    blue, orange = "#2a78d6", "#eb6834"
    ink, muted, grid = "#0b0b0b", "#52514e", "#e6e5e0"
    labels = [w["start"][5:].replace("-", "/") for w in history]
    x = range(len(history))
    partial = [w.get("partial", False) for w in history]

    fig, axes = plt.subplots(2, 2, figsize=(12, 7), dpi=110)
    fig.patch.set_facecolor("#fcfcfb")

    def style(ax, title, unit):
        ax.set_facecolor("#fcfcfb")
        ax.set_title(title, loc="left", fontsize=12, color=ink, fontweight="bold")
        ax.set_ylabel(unit, color=muted, fontsize=9)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=8, color=muted)
        ax.tick_params(axis="y", labelsize=8, colors=muted, length=0)
        ax.tick_params(axis="x", length=0)
        ax.grid(axis="y", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color(grid)

    def line(ax, values, color=blue):
        pts = [(i, v) for i, v in zip(x, values) if v is not None]
        if pts:
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=5)
        ax.set_ylim(bottom=0)

    # 1) 배포 빈도: 성공 + 실패 누적 막대
    ax = axes[0][0]
    ok = [w["deployments"] for w in history]
    bad = [w["failed_deployments"] for w in history]
    for i in x:
        a = 0.45 if partial[i] else 1.0
        ax.bar(i, ok[i], width=0.6, color=blue, alpha=a, label="Success" if i == 0 else None)
        ax.bar(i, bad[i], width=0.6, bottom=ok[i], color=orange, alpha=a,
               edgecolor="#fcfcfb", linewidth=2, label="Failed" if i == 0 else None)
    style(ax, "Deployment Frequency", "deploys / week")
    ax.legend(frameon=False, fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2)

    line(axes[0][1], [w["lead_time_hours"] for w in history])
    style(axes[0][1], "Lead Time for Changes (median)", "hours")

    line(axes[1][0], [w["mttr_hours"] for w in history])
    style(axes[1][0], "Time to Restore (MTTR, mean)", "hours")

    line(axes[1][1], [w["change_failure_rate"] for w in history])
    style(axes[1][1], "Change Failure Rate", "%")

    fig.suptitle(f"DORA metrics  ·  {repo}  ·  weekly (KST, Mon–Sun)", x=0.01, ha="left",
                 fontsize=11, color=muted)
    fig.text(0.99, 0.005, "faded bar = current week (in progress)", ha="right", fontsize=8, color=muted)
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return True


def render_readme_section(latest: dict, history: list[dict], chart_rel: str | None, demo: bool) -> str:
    cur, last, agg = latest["current_week"], latest["last_week"], latest["summary"]
    rows = [
        ("배포 빈도", f"{cur['deployments']}회", f"{last['deployments']}회",
         f"주 {agg['deployments_per_week']}회", grade("deployments_per_week", agg["deployments_per_week"])),
        ("변경 리드 타임", fmt_hours(cur["lead_time_hours"]), fmt_hours(last["lead_time_hours"]),
         fmt_hours(agg["lead_time_hours"]), grade("lead_time_hours", agg["lead_time_hours"])),
        ("평균 복구 시간 (MTTR)", fmt_hours(cur["mttr_hours"]), fmt_hours(last["mttr_hours"]),
         fmt_hours(agg["mttr_hours"]), grade("mttr_hours", agg["mttr_hours"])),
        ("변경 실패율", fmt_pct(cur["change_failure_rate"]), fmt_pct(last["change_failure_rate"]),
         fmt_pct(agg["change_failure_rate"]), grade("change_failure_rate", agg["change_failure_rate"])),
    ]
    gen = parse_ts(latest["generated_at"]).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    n = latest["config"]["history_weeks"]
    out = [MARK_START, "", "## 3. 주간 리포트 (자동 생성)", ""]
    if demo:
        out += ["> ⚠️ **데모 데이터입니다.** 실제 저장소 기록이 아닙니다.", ""]
    run = f" · [수집 실행 로그]({latest['run_url']})" if latest.get("run_url") else ""
    out += [
        f"> 자동 생성: {gen} · 이번 주: {cur['start']} ~ (진행 중) · 지난 주: {last['start']} ~ {last['end']}{run}",
        "",
        f"| 지표 | 이번 주 (진행 중) | 지난 주 | 최근 {n}주 | 등급 (최근 {n}주) |",
        "|---|---:|---:|---:|:---:|",
    ]
    out += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
    out.append("")
    if latest.get("open_failure_since"):
        since = parse_ts(latest["open_failure_since"]).astimezone(KST).strftime("%m/%d %H:%M")
        out += [f"> 🚨 {since}에 실패한 배포가 아직 복구되지 않았습니다.", ""]
    if chart_rel:
        out += [f"![DORA 지표 추이]({chart_rel})", ""]
    out += [
        "<details><summary>주별 기록 펼치기</summary>",
        "",
        "| 주 (월요일) | 배포 | 실패 | 리드 타임 | MTTR | 실패율 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for w in reversed(history[-12:]):
        tag = " (진행 중)" if w.get("partial") else ""
        out.append(f"| {w['start']}{tag} | {w['deployments']} | {w['failed_deployments']} | "
                   f"{fmt_hours(w['lead_time_hours'])} | {fmt_hours(w['mttr_hours'])} | "
                   f"{fmt_pct(w['change_failure_rate'])} |")
    out += ["", "</details>", "",
            "원본 데이터: [`metrics/latest.json`](metrics/latest.json) · "
            "[`metrics/history.json`](metrics/history.json)", "", MARK_END]
    return "\n".join(out)


def update_readme(path: Path, section: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else "# DORA Metrics\n"
    pattern = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _: section, text)
    else:
        text = text.rstrip() + "\n\n" + section + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="DORA 4대 지표 수집")
    ap.add_argument("--demo", action="store_true", help="가짜 데이터로 미리보기 (demo-output/ 에 기록)")
    ap.add_argument("--out", help="출력 폴더 (기본: 저장소 루트, --demo 이면 demo-output)")
    args = ap.parse_args()

    cfg = {
        "repo": env("GITHUB_REPOSITORY", "demo/demo" if args.demo else None),
        "deploy_workflow": env("DEPLOY_WORKFLOW", "deploy.yml"),
        "branch": env("DEPLOY_BRANCH", "main"),
        "incident_label": env("INCIDENT_LABEL", "incident"),
        "history_weeks": int(env("HISTORY_WEEKS", "8")),
    }
    out_root = Path(args.out) if args.out else (ROOT / "demo-output" if args.demo else ROOT)

    if args.demo:
        gh = DemoGitHub()
    else:
        if not cfg["repo"]:
            print("GITHUB_REPOSITORY(예: myname/myrepo) 환경 변수가 필요합니다.")
            return 1
        gh = GitHub(env("GITHUB_API_URL", "https://api.github.com"), cfg["repo"], env("GITHUB_TOKEN"))

    now = datetime.now(timezone.utc)
    this_week = week_start(now)
    weeks = [this_week - timedelta(weeks=i) for i in range(cfg["history_weeks"] - 1, -1, -1)]

    samples = collect_samples(gh, cfg, weeks)

    recomputed = []
    for ws in weeks:
        we = ws + timedelta(weeks=1)
        partial = ws == this_week
        entry = summarize(samples, ws, min(we, now) if partial else we, 1)
        entry.update({
            "week": f"{ws.isocalendar()[0]}-W{ws.isocalendar()[1]:02d}",
            "start": ws.date().isoformat(),
            "end": (we - timedelta(days=1)).date().isoformat(),
            "partial": partial,
        })
        recomputed.append(entry)

    # 기록 병합: 최근 N주는 새로 계산한 값으로 덮어쓰고, 그 이전 기록은 보존
    metrics_dir = out_root / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    hist_path = metrics_dir / "history.json"
    old = json.loads(hist_path.read_text(encoding="utf-8")).get("weeks", []) if hist_path.exists() else []
    merged = {w["week"]: w for w in old}
    merged.update({w["week"]: w for w in recomputed})
    history = sorted(merged.values(), key=lambda w: w["start"])[-52:]

    summary = summarize(samples, weeks[0], now, len(weeks) - 1 + (now - this_week) / timedelta(weeks=1))
    summary["grades"] = {k: grade(k, summary[k]) for k in
                         ("deployments_per_week", "lead_time_hours", "mttr_hours", "change_failure_rate")}
    latest = {
        "generated_at": now.isoformat(),
        "repository": cfg["repo"],
        "demo": args.demo,
        "run_url": env("RUN_URL"),
        "config": cfg,
        "current_week": recomputed[-1],
        "last_week": recomputed[-2] if len(recomputed) > 1 else recomputed[-1],
        "summary": summary,
        "open_failure_since": samples["open_failure_since"].isoformat() if samples["open_failure_since"] else None,
        "definitions": DEFINITIONS,
    }

    def dump(p: Path, obj) -> None:
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    dump(metrics_dir / "latest.json", latest)
    dump(hist_path, {"repository": cfg["repo"], "updated_at": now.isoformat(), "weeks": history})

    chart = out_root / "docs" / "dora" / "dora-chart.png"
    chart_ok = render_chart(history[-cfg["history_weeks"]:], chart, cfg["repo"])
    chart_rel = "docs/dora/dora-chart.png" if chart_ok else None
    update_readme(out_root / "README.md",
                  render_readme_section(latest, history[-cfg["history_weeks"]:], chart_rel, args.demo))

    s = summary
    print(f"[DORA] {cfg['repo']}  (최근 {cfg['history_weeks']}주)")
    print(f"  배포 빈도   : 주 {s['deployments_per_week']}회   {s['grades']['deployments_per_week']}")
    print(f"  리드 타임   : {fmt_hours(s['lead_time_hours'])}   {s['grades']['lead_time_hours']}")
    print(f"  MTTR        : {fmt_hours(s['mttr_hours'])}   {s['grades']['mttr_hours']}")
    print(f"  변경 실패율 : {fmt_pct(s['change_failure_rate'])}   {s['grades']['change_failure_rate']}")
    print(f"  출력 위치   : {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
