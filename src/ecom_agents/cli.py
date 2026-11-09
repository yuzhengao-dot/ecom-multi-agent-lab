"""Command line interface: run campaigns, inspect users, serve the API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .config import Settings
from .experiments import describe
from .runner import run_campaign
from .schemas import CampaignRequest
from .tools.repository import Repository


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover - non-standard streams
        pass


def _human_summary(response: Any) -> str:
    lines = [
        f"trace_id   : {response.trace_id}",
        f"variant    : {response.variant}   mode: {response.mode}",
        f"segment    : {response.plan.segment}",
        f"headline   : {response.plan.headline}",
        f"body       : {response.plan.body}",
        f"cta        : {response.plan.cta}",
        f"SKUs       : {', '.join(response.plan.recommended_skus) or '无'}",
        f"discount   : {response.plan.discount_rate * 100:.0f}%   coupon: {response.plan.coupon_code or '无'}",
        f"inventory  : {response.plan.inventory_notes}",
        f"compliance : {response.plan.compliance_status} {response.plan.compliance_notes}",
        f"trace      : {response.trace.elapsed_ms:.0f} ms, {response.trace.llm_tokens} tokens, "
        f"degraded={response.trace.degraded_agents}",
        "",
        "agents:",
    ]
    for result in response.agent_results:
        lines.append(f"  - {result.agent:<15} {result.summary}  ({result.duration_ms:.1f} ms)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecom-agent", description="电商营销多智能体实验台")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一次营销决策")
    run.add_argument("--user", required=True, help="用户 ID，例如 U1001")
    run.add_argument("--scene", default="日常触达")
    run.add_argument("--goal", default="提升复购")
    run.add_argument("--channel", default="app_push", choices=["app_push", "sms", "email", "in_app", "wechat"])
    run.add_argument("--budget", type=float, default=0.0)
    run.add_argument("--top-n", type=int, default=3)
    run.add_argument("--experiment", default="campaign_v1")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--json", action="store_true", help="输出完整 JSON 响应")

    sub.add_parser("users", help="列出示例用户")

    variant = sub.add_parser("variant", help="查看 A/B 分桶结果")
    variant.add_argument("--user", required=True)
    variant.add_argument("--experiment", default="campaign_v1")

    serve = sub.add_parser("serve", help="启动 HTTP 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()

    if args.command == "run":
        request = CampaignRequest(
            user_id=args.user,
            scene=args.scene,
            goal=args.goal,
            channel=args.channel,
            budget=args.budget,
            top_n=args.top_n,
            experiment_key=args.experiment,
            dry_run=args.dry_run,
        )
        response = run_campaign(request, settings)
        if args.json:
            print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            print(_human_summary(response))
        return 0

    if args.command == "users":
        repo = Repository(settings.db_path, settings.seed_path)
        print(json.dumps(repo.list_users(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "variant":
        print(json.dumps(describe(args.user, args.experiment, settings.ab_split, settings.ab_enabled), ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve":
        import uvicorn

        uvicorn.run("ecom_agents.app:app", host=args.host, port=args.port, reload=False)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
