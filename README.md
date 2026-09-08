# The `unipersian` package

Persian language support for LaTeX, LuaLaTeX, and XeLaTeX using Babel.

## Description

This package provides a convenient interface for typesetting Persian as the main language, with English text in LaTeX, based on the Babel package and using LuaTeX (recommended), XeTeX (fallback), or pdfTeX (last resort) as the underlying engine. It provides Persian captions and numerals, bidirectional text support, and adaptations for common document classes and Beamer.

## Features

- Persian captions and Persian numerals (Arabic-Indic digits, Abjad,
  `adadi`, `harfi`, `tartibi`).
- Direction commands `\lr{...}` and `\rl{...}`.
- Automatic Beamer detection with adapted list labels.
- Book/report adaptations and hyperref-safe bookmarks and labels.

## Installation

Manual installation from GitHub:

~~~bash
git clone https://github.com/gambi-shah/unipersian.git
~~~

Copy `unipersian.sty` and the `unipersian-*.def` files into a TeX-searchable
directory (run `mktexlsr` if needed) or keep them next to your document.

## Usage

Minimal example — no font setup required:

~~~latex
\documentclass{article}
\usepackage{unipersian}

\begin{document}
سلام دنیا!
\end{document}
~~~

Compile with LuaLaTeX (recommended), XeLaTeX (fallback) or pdfTeX (last resort).

`UniPersian` accepts the same package options as `babel`; see the Babel
manual for the complete list.

## Core APIs

The four core APIs are:

- `\settextfont[opts]{font}` — selects text font for Unicode
  engines.
- `\lr{...}` — typesets an inline english run.
- `\rl{...}` — typesets an inline persian run.
- `latin` environment — typesets a longer english block:

The package also provides further APIs for captions, numerals, counters,
and document classes; see the [main manual](doc/unipersian.pdf) for the complete documentation.

## License and maintainer

- License: LPPL 1.3c or later.
- Maintainer: Amer Amikhteh.
- Report bugs at the repository issue tracker.
