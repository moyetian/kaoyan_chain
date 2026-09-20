"""菜单参数解析，供 CLI 包装入口使用。"""
import argparse


def parse_menu_args(argv):
    parser = argparse.ArgumentParser(prog="ky menu")
    parser.add_argument("action", nargs="?")
    parser.add_argument("--action", "-a", dest="explicit_action")
    parser.add_argument("--list", "-l", action="store_true")
    for option in ("school1", "school2", "major", "keyword", "new", "old", "file", "subject"):
        aliases = {"keyword": "-k", "file": "-f"}
        flags = ["--" + option] + ([aliases[option]] if option in aliases else [])
        parser.add_argument(*flags, default="")
    args = vars(parser.parse_args(argv))
    action = args.pop("explicit_action") or args.pop("action")
    args.pop("action", None)
    listed = args.pop("list")
    return action, listed, args
