import asyncio
from dataclasses import asdict
from pathlib import Path
import sys
from argparse import ArgumentParser, Namespace

from dotenv import dotenv_values, load_dotenv
import rich
import rich.box
import rich.console
import rich.table
import typst

from reportobello import ReportobelloApi, ReportobelloMissingApiKey, ReportobelloTemplateNotFound, Template


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

    show_all = bool(arg.a)

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

    for template in templates:
        if show_all:
            table.add_row(template.name, str(template.version), template.content)

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
        template = (await api.get_template_versions(arg.template))[0]

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

    compiler = get_typst_compiler(input_file, arg.env or [])

    try:
        compiler.compile(output_file)

    except RuntimeError as ex:
        print(f"\x1b[31m{str(ex).strip()}\x1b[0m\n", sys.stderr)
        sys.exit(1)

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



async def async_main() -> None:
    parser = ArgumentParser(description="Reportobello CLI")

    subparsers = parser.add_subparsers()

    ls = subparsers.add_parser("ls")
    ls.add_argument("template", nargs="?", help="Only show a specific template. Defaults to all templates")
    ls.add_argument("-a", action="store_true", help="Show all data")
    ls.add_argument("--format", choices=("pretty", "json"), default="pretty", help="Change output format")
    ls.set_defaults(func=ls_command)

    push = subparsers.add_parser("push")
    push.add_argument("filename", help="File to upload")
    push.add_argument("template", nargs="?", help="Template name to use. Defaults to filename without extension")
    push.set_defaults(func=push_command)

    pull = subparsers.add_parser("pull")
    pull.add_argument("template", help="Template to download")
    pull.add_argument("filename", nargs="?", help="Download location. Defaults to template name with `.typ` extension added")
    pull.set_defaults(func=pull_command)

    rm = subparsers.add_parser("rm")
    rm.add_argument("template", help="Template name to delete")
    rm.set_defaults(func=rm_command)

    build = subparsers.add_parser("build")
    build.add_argument("template", help="Template file to build")
    build.add_argument("--env", metavar="KEY=VALUE", action="append", help="Pass an environment variable to the template")
    build.set_defaults(func=build_command)

    watch = subparsers.add_parser("watch")
    watch.add_argument("template", help="Template file to watch")
    watch.add_argument("--env", metavar="KEY=VALUE", action="append", help="Pass an environment variable to the template")
    watch.set_defaults(func=watch_command)

    args = parser.parse_args(sys.argv[1:])

    if hasattr(args, "func"):
        await args.func(args)

    else:
        parser.print_help()


def main():
    try:
        asyncio.run(async_main())

    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()
