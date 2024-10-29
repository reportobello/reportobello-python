import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from difflib import unified_diff
from argparse import ArgumentParser, Namespace

from dotenv import dotenv_values, load_dotenv
import httpx
import rich
import rich.box
import rich.console
import rich.table
from rich.text import Text
import typst

from reportobello import ReportobelloApi, ReportobelloMissingApiKey, ReportobelloTemplateNotFound, ReportobelloUnauthorized, Template


load_dotenv()

env_file = {k: v or "" for k, v in dotenv_values().items()}

# TODO: handle timeout/invalid host errors


def get_api() -> ReportobelloApi:
    try:
        return ReportobelloApi()

    except ReportobelloMissingApiKey:
        print("Missing API key. Set the REPORTOBELLO_API_KEY env var", file=sys.stderr)
        sys.exit(1)


console = rich.console.Console()


async def ls_command(arg: Namespace):
    api = get_api()

    if arg.template:
        try:
            templates = await api.get_template_versions(arg.template)

        except ReportobelloTemplateNotFound:
            print("Error: Template not found", file=sys.stderr)
            sys.exit(1)

    else:
        templates = await api.get_templates()

    show_diff = bool(arg.diff)
    show_all = show_diff or bool(arg.all)

    if arg.format == "json":
        data = [asdict(t) for t in templates]

        for row in data:
            del row["file"]

            if not show_all:
                del row["content"]

        console.print_json(data=data)
        return

    table = rich.table.Table(box=rich.box.ROUNDED, show_lines=True)

    table.add_column("Name")
    table.add_column("Version")

    if show_all:
        table.add_column("Template")

    for i, template in enumerate(templates):
        if show_all:
            if show_diff and i < len(templates) - 1:
                a = (templates[i + 1].content or "").splitlines()
                b = (template.content or "").splitlines()

                lines = list(unified_diff(a, b, lineterm=""))

                if not lines:
                    diff = Text("No diff", style="bright_black")

                else:
                    lines = lines[2:]
                    items = []

                    for i, line in enumerate(lines):
                        if i < len(lines) - 1:
                            line += "\n"

                        if line.startswith("-"):
                            items.append((line, "red"))
                        elif line.startswith("+"):
                            items.append((line, "green"))
                        elif line.startswith("@"):
                            items.append((line, "cyan"))
                        else:
                            items.append(line)

                    diff = Text.assemble(*items)

            else:
                diff = template.content

            table.add_row(template.name, str(template.version), diff)

        else:
            table.add_row(template.name, str(template.version))

    console.print(table)


async def push_command(arg: Namespace):
    api = get_api()

    file = Path(arg.filename)

    if not file.exists():
        print("File does not exist", file=sys.stderr)
        sys.exit(1)

    # TODO: if file is a PDF, convert then upload
    template_name = arg.template if arg.template else file.stem

    template = await api.create_or_update_template(Template(name=template_name, file=file))

    print(f"Uploaded template `{template.name}` v{template.version} successfully!")


async def pull_command(arg: Namespace):
    api = get_api()

    try:
        templates = await api.get_template_versions(arg.template)

        if arg.version == -1:
            template = templates[0]
        else:
            for t in templates:
                if t.version == arg.version:
                    template = t
                    break
            else:
                print(f"Template version {arg.version} was not found (latest is v{templates[0].version})", file=sys.stderr)
                sys.exit(1)

    except ReportobelloTemplateNotFound:
        print(f"Template `{arg.template}` was not found", file=sys.stderr)
        sys.exit(1)

    Path(template.name + ".typ").write_text(template.content or "")

    print(f"Downloaded template `{template.name}` v{template.version} successfully!")


async def rm_command(arg: Namespace):
    api = get_api()

    try:
        await api.delete_template(arg.template)

        print(f"Removed template `{arg.template}` successfully!")

    except ReportobelloTemplateNotFound:
        print(f"Could not find template `{arg.template}`", file=sys.stderr)
        sys.exit(1)


def get_typst_compiler(file: Path, env_vars: list[str]) -> typst.Compiler:
    env_args = dict(env.split("=", maxsplit=1) for env in env_vars)

    return typst.Compiler(file, sys_inputs=env_file | env_args)


async def build_command(arg: Namespace):
    input_file = Path(arg.template)
    output_file = input_file.with_suffix(".pdf")

    if arg.local:
        if arg.json:
            print("Warning: `--json` is ignored when `--local` is set")

        compiler = get_typst_compiler(input_file, arg.env or [])

        try:
            compiler.compile(output_file)

        except RuntimeError as ex:
            print(f"\x1b[31m{str(ex).strip()}\x1b[0m\n", sys.stderr)
            sys.exit(1)

    else:
        if arg.env:
            print("Warning: `--env` is ignored when `--local` is unset (for now)")

        api = get_api()

        if arg.json == "-":
            data = json.loads(sys.stdin.read())
        else:
            data = json.loads(Path(arg.json or "data.json").read_text())

        try:
            pdf = await api.build_template(Template(name=arg.template), data)

        except ReportobelloTemplateNotFound as ex:
            if arg.template.endswith((".typ", ".typst")):
                print(ex)
                print(f"Did you mean to build `{Path(arg.template).with_suffix('')}`?")
                sys.exit(1)

            else:
                raise

        await pdf.save_to(output_file)

    print(f"Saving PDF to {output_file}")


async def watch_command(arg: Namespace):
    input_file = Path(arg.template)
    output_file = input_file.with_suffix(".pdf")

    compiler = get_typst_compiler(input_file, arg.env or [])

    old_mtime = 0

    while True:
        try:
            mtime = input_file.stat().st_mtime

        except FileNotFoundError:
            await asyncio.sleep(0.01)
            continue

        if mtime <= old_mtime:
            await asyncio.sleep(0.01)
            continue

        old_mtime = mtime

        try:
            print(f"Saving PDF to {output_file}")

            compiler.compile(output_file)

        except RuntimeError as ex:
            print(f"\x1b[31m{str(ex).strip()}\x1b[0m\n", file=sys.stderr)

        await asyncio.sleep(0.01)


async def builds_ls_command(arg: Namespace):
    api = get_api()

    reports = await api.get_recent_builds(arg.template)

    if arg.format == "json":
        out = []

        for report in reports:
            d = asdict(report)
            d["started_at"] = report.started_at.isoformat()
            d["finished_at"] = report.finished_at.isoformat()

            out.append(d)

        console.print_json(data=out)

    else:
        table = rich.table.Table(box=rich.box.ROUNDED, show_lines=True)

        table.add_column("Started at")
        table.add_column("Finished at")
        table.add_column("Requested/Used version")
        table.add_column("Filename")
        table.add_column("Error")

        for report in reports:
            requested_version = "latest" if report.requested_version == -1 else report.requested_version

            table.add_row(
                report.started_at.isoformat(),
                report.finished_at.isoformat(),
                f"{requested_version} / {report.actual_version}",
                report.filename,
                Text((report.error_message or "").strip(), style="red"),
            )

        console.print(table)


async def env_ls_command(arg: Namespace):
    api = get_api()

    env_vars = await api.get_env_vars()

    if getattr(arg, "format", None) == "json":
        console.print_json(data=env_vars)

    else:
        table = rich.table.Table(box=rich.box.ROUNDED, show_lines=True)

        table.add_column("Key")
        table.add_column("Value")

        for k, v in env_vars.items():
            table.add_row(k, v)

        console.print(table)


async def env_set_command(arg: Namespace):
    api = get_api()

    await api.update_env_vars({arg.key: arg.value})

    print("Environment variables updated!")


async def env_rm_command(arg: Namespace):
    api = get_api()

    await api.delete_env_vars(keys=arg.key)

    print("Environment variables deleted!")


async def async_main() -> None:
    parser = ArgumentParser(description="Reportobello CLI")

    subparsers = parser.add_subparsers()

    ls = subparsers.add_parser("ls")
    ls.add_argument("template", nargs="?", help="Only show a specific template. Defaults to all templates")
    ls.add_argument("-a", "--all", action="store_true", help="Show all data")
    ls.add_argument("--diff", action="store_true", help="Only show diffs between templates. Implies `-a`")
    ls.add_argument("--format", choices=("pretty", "json"), default="pretty", help="Change output format")
    ls.set_defaults(func=ls_command)

    push = subparsers.add_parser("push")
    push.add_argument("filename", help="File to upload")
    push.add_argument("template", nargs="?", help="Template name to use. Defaults to filename without extension")
    push.set_defaults(func=push_command)

    pull = subparsers.add_parser("pull")
    pull.add_argument("template", help="Template to download")
    pull.add_argument("-v", "--version", type=int, default=-1, help="Template to download")
    pull.add_argument("filename", nargs="?", help="Download location. Defaults to template name with `.typ` extension added")
    pull.set_defaults(func=pull_command)

    rm = subparsers.add_parser("rm")
    rm.add_argument("template", help="Template name to delete")
    rm.set_defaults(func=rm_command)

    build = subparsers.add_parser("build")
    build.add_argument("template", help="Template file to build")
    build.add_argument("json", nargs="?", help="JSON data to use for the report. Use `-` for stdin. Defaults to `data.json`. Ignored if `--local` is set")
    build.add_argument("--local", action="store_true", help="Build report using the Reportobello instance instead of building locally")
    build.add_argument("--env", metavar="KEY=VALUE", action="append", help="Pass an environment variable to the template. Currently this is only used when `--local` is set")
    build.set_defaults(func=build_command)

    watch = subparsers.add_parser("watch")
    watch.add_argument("template", help="Template file to watch")
    watch.add_argument("--env", metavar="KEY=VALUE", action="append", help="Pass an environment variable to the template")
    watch.set_defaults(func=watch_command)

    builds = subparsers.add_parser("builds")
    builds_subparser = builds.add_subparsers()
    builds_ls = builds_subparser.add_parser("ls")
    builds_ls.add_argument("template", help="Show recent builds for template")
    builds_ls.add_argument("--format", choices=("pretty", "json"), default="pretty", help="Change output format")
    builds_ls.set_defaults(func=builds_ls_command)

    env = subparsers.add_parser("env")
    env.set_defaults(func=env_ls_command)
    env_subparser = env.add_subparsers()
    env_ls = env_subparser.add_parser("ls")
    env_ls.add_argument("--format", choices=("pretty", "json"), default="pretty", help="Change output format")
    env_ls.set_defaults(func=env_ls_command)
    env_set = env_subparser.add_parser("set")
    env_set.add_argument("key")
    env_set.add_argument("value")
    env_set.set_defaults(func=env_set_command)
    env_rm = env_subparser.add_parser("rm")
    env_rm.add_argument("key", nargs="+")
    env_rm.set_defaults(func=env_rm_command)

    args = parser.parse_args(sys.argv[1:])

    if hasattr(args, "func"):
        await args.func(args)

    else:
        parser.print_help()


def main():
    try:
        asyncio.run(async_main())

    except ReportobelloUnauthorized:
        err = """\
Unauthorized: Is your API key or host URL set incorrectly?

Make sure you setup your `.env `file properly:

REPORTOBELLO_API_KEY="rpbl_..."
REPORTOBELLO_HOST="https://example.com"

Or `export` the env vars in your terminal:

export REPORTOBELLO_API_KEY="rpbl_..."
export REPORTOBELLO_HOST="https://example.com"
"""

        print(err, file=sys.stderr)
        sys.exit(1)

    except httpx.ConnectError:
        api = get_api()

        print(f"Could not connect to `{api.client.base_url}`", file=sys.stderr)
        sys.exit(1)

    except httpx.UnsupportedProtocol:
        api = get_api()

        print(f"Invalid URL `{api.client.base_url}`. Did you forget to add `https://` or `http://`?", file=sys.stderr)
        sys.exit(1)

    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()
