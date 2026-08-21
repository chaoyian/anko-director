from __future__ import annotations

import argparse
import json
import threading
import webbrowser

import uvicorn

from .config import ConfigStore
from .dice import execute_proposal, parse_dice_notation
from .schemas import DiceOperation, RollProposal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anko-director")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="启动本地导演台")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-browser", action="store_true")
    roll = subparsers.add_parser("roll", help="执行并输出可复现骰子结果")
    roll.add_argument("notation", help="例如 d100、2d20kh1、3d6+2")
    roll.add_argument("--seed", help="用于复现的十六进制种子")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "roll":
        spec = parse_dice_notation(args.notation)
        proposal = RollProposal(
            title=f"命令行投骰 {args.notation}",
            operations=[DiceOperation(id="cli_roll", label=args.notation, dice=spec)],
        )
        print(json.dumps(execute_proposal(proposal, args.seed), ensure_ascii=False, indent=2))
        return
    settings = ConfigStore().load()
    host = getattr(args, "host", None) or settings.host
    port = getattr(args, "port", None) or settings.port
    no_browser = getattr(args, "no_browser", False)
    if settings.open_browser and not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    from .web.app import create_app

    uvicorn.run(create_app(), host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
