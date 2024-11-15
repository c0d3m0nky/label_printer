import sys
import textwrap
from math import ceil
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict
import re

import labels
from reportlab.graphics import shapes
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from tap import Tap

_script_root = Path(__file__).resolve().parent
_line_break = '#%'

pdfmetrics.registerFont(TTFont('Roboto', './RobotoMono-VariableFont_wght.ttf'))

_default_font = 'Roboto'
_default_font_size = 9


def chunk_str(cstr, max_len):
    chunks = ceil(len(cstr) / max_len)
    res = []

    for idx in range(0, chunks):
        i = (cstr[(idx * max_len): (idx * max_len) + max_len]).rstrip()
        if idx != 0 and i.startswith(' '):
            i = i[1:]
        res.append(i)

    return res


@dataclass
class LabelMeta:
    specs: labels.Specification
    font: str
    font_size: int
    subtext_font_size: int
    text_line_max_len: int
    max_text_lines: int
    qr_text_pad: int
    include_timestamp: bool

    rows = property(lambda self: self.rows)
    cols = property(lambda self: self.columns)


@dataclass
class LabelLine:
    text: str
    font_name: str
    font_size: int


class LabelData:
    url: str = ''
    lines: List[LabelLine]
    meta: LabelMeta

    def __init__(self, url: str, text: str, label_meta: LabelMeta):
        self.meta = label_meta
        self.url = url
        self.lines = []

        if _line_break in text:
            text_lines = [l.rstrip() for l in text.split(_line_break)]
        else:
            text_lines = [text.rstrip()]

        def break_word(lines) -> List[str]:
            wrapped_lines = []

            for line in lines:
                wrapped_lines += textwrap.wrap(line, label_meta.text_line_max_len)

            return wrapped_lines

        def break_any(lines) -> List[str]:
            wrapped_lines = []

            for line in lines:
                wrapped_lines += [line] if len(line) <= label_meta.text_line_max_len else [l for l in chunk_str(line, label_meta.text_line_max_len)]

            return wrapped_lines

        if _args.break_on_any:
            broken_lines = break_any(text_lines)
        else:
            broken_lines = break_word(text_lines)

            if len(broken_lines) > label_meta.max_text_lines:
                broken_lines = break_any(text_lines)

        if len(broken_lines) > label_meta.max_text_lines:
            raise ValueError(f'Text too long: {text}')

        self.lines = [LabelLine(line, label_meta.font, label_meta.font_size) for line in broken_lines]

        if label_meta.include_timestamp:
            self.lines.append(LabelLine(datetime.now().strftime("%y-%m-%d"), label_meta.font, label_meta.subtext_font_size))


_label_meta: Dict[str, LabelMeta] = {
    'avery-5160': LabelMeta(
        labels.Specification(
            sheet_width=215.9,
            sheet_height=279.4,
            columns=3,
            rows=10,
            label_width=66,
            label_height=25.4,
            corner_radius=2,
            left_margin=5, right_margin=5, top_margin=13,
            left_padding=0, right_padding=1, top_padding=1,
            bottom_padding=1,
            row_gap=0
        ),
        _default_font, _default_font_size, subtext_font_size=(_default_font_size - 3),
        text_line_max_len=21,
        max_text_lines=4,
        qr_text_pad=3,
        include_timestamp=True
    )
}

_rx_url = re.compile(r'(https?://)?(.+[^/.]+\.[^/.]+)(/?.+)?')


class Args(Tap):
    label_type: LabelMeta
    skip: int
    data: List[LabelData]
    break_on_any: bool

    def configure(self) -> None:
        self.add_argument('label_type', type=str, choices=_label_meta.keys(), help='Type of label')
        self.add_argument('skip', type=int, help='Number of label slots to skip')
        self.add_argument('--break-on-any', action='store_true', help='Break line on any char', default=False)
        self.add_argument('data', type=str, nargs='+', help=f'Tilde delimited data, use {_line_break} for line break')

    def process_args(self) -> None:
        # noinspection PyTypeChecker
        self.label_type = _label_meta[self.label_type]
        # noinspection PyTypeChecker
        data_interim: List[str] = self.data

        if len(data_interim) > 0 and self.label_type:
            self.data = []

            for data_str in data_interim:
                d = data_str.split('~')

                if len(d) != 2:
                    raise ValueError(f'Invalid data: {data_str}')

                url = d[0]
                label_text = d[1]

                m = _rx_url.match(url)

                if not m:
                    raise ValueError(f'Invalid url: {url}')

                if not m.group(1):
                    url = 'https://' + url

                self.data.append(LabelData(url, label_text, self.label_type))
        else:
            raise ValueError('Invalid data')

    def error(self, message):
        print('error: %s\n' % message)
        self.print_help()
        sys.exit(2)


_args: Args


def make_qr(data, error="L", version=None, compress=None, **kwargs):
    return qr.QrCodeWidget(data, barLevel=error, qrVersion=version, **kwargs)


def draw_address(label, width, height, data: LabelData):
    assert data

    # The order is flipped, because we're painting from bottom to top.
    data.lines.reverse()

    text_x = (height * 1.01) + data.meta.qr_text_pad

    group = shapes.Group()
    x, y = 0, 3
    for line in data.lines:
        if not line:
            continue
        shape = shapes.String(x, y, line.text, textAnchor="start", fontName=line.font_name, fontSize=line.font_size)
        _, _, _, y = shape.getBounds()
        # Some extra spacing between the lines, to make it easier to read
        y += 3
        group.add(shape)
    _, _, label_x_bound, label_y_bound = label.getBounds()
    _, _, text_x_bound, text_y_bound = group.getBounds()

    # Make sure the label fits in a sticker
    assert text_x_bound <= (label_x_bound - text_x), (data, text_x_bound, label_x_bound)
    assert text_y_bound <= label_y_bound, (data, text_y_bound, label_y_bound)

    group.translate(text_x, height - y)

    qrc = qr.QrCodeWidget(data.url, barLevel="M")
    b = qrc.getBounds()

    w = (b[2] - b[0]) / 1.1
    h = (b[3] - b[1]) / 1.1

    d = Drawing(w, h, transform=[height / w, 0, 0, height / h, 0, 0])

    d.add(qrc)

    label.add(d)
    label.add(group)


def main():
    global _args

    _args = Args()

    try:
        _args = _args.parse_args()
        specs = _args.label_type.specs

        sheet = labels.Sheet(specs, draw_address, border=False)
        skip_cells = []

        for si in range(0, _args.skip):
            skip_cells.append(((si // specs.columns) + 1, (si % specs.columns) + 1))

        sheet.partial_page(1, skip_cells)

        for d in _args.data:
            sheet.add_label(d)

        sheet.save((_script_root / 'output.pdf').as_posix())
        print("{0:d} label(s) output on {1:d} page(s).".format(sheet.label_count, sheet.page_count))
    except ValueError as e:
        print('error: %s\n' % e)
        _args.print_help()
        sys.exit(2)


if __name__ == '__main__':
    main()
