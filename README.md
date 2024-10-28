# Reportobello

Python SDK for the Reportobello API.

This package also includes the Reportobello CLI, `rpbl`.
To learn more about the CLI, read the docs [here](https://reportobello.com/docs/cli.html).

## Installing

```shell
$ pip install reportobello
```

## Basic Usage

Below is a full exmaple of how to create reports in Reportobello.

This example is ready to go, and can be copy-pasted into your project.

```python
from dataclasses import dataclass
import asyncio

from reportobello import ReportobelloApi, Template


@dataclass
class QuarterlyReport(Template):
    name = "quarterly_report"

    # See Typst docs for syntax: https://typst.app/docs
    content = """
#let data = json("data.json")

= Q#data.quarter Earnings Report

Generated: #datetime.today().display()

Earnings: #data.earnings
"""
    # Alternatively, store in a file
    # file = "report.typ"

    quarter: int
    earnings: float


api = ReportobelloApi()


async def main():
    template = QuarterlyReport(quarter=1, earnings=123_456)

    # You only need to run this if the template above changes
    await api.create_or_update_template(template)

    pdf = await api.build_template(template)

    print(f"Downloading {pdf.url}")

    await pdf.save_to("output.pdf")

asyncio.run(main())
```

Read [the docs](https://reportobello.com/docs/libraries/python.html) for more info.
