LaTeX Package: Unipersian v0.3.0 2026-10-21
----------------------------------------

Persian typesetting for LaTeX, LuaLaTeX, and XeLaTeX using BabelBidi (based on Babel).

## Description

UniPersian provides Persian typesetting with LuaLaTeX (recommended), XeLaTeX, and pdfTeX (basic support), using BabelBidi (based on Babel). It supports Persian as the main or a secondary language, with Persian captions, numerals, and font configuration.


## Features

- Persian captions and numerals, including Abjad, `adadi`, `harfi`, and `tartibi`.
- Font configuration for Persian and Latin text.
- Automatic character-based language and font selection under LuaLaTeX.
- Adaptations for common document classes and Beamer.
- Integration with `minted` and `listings` for code containing Persian text.

## Installation

### From CTAN

https://ctan.org/pkg/unipersian

With TeX Live:

~~~bash
tlmgr install unipersian
~~~

### From GitHub

~~~bash
git clone https://github.com/gambi-shah/unipersian.git
~~~

Copy the package files into a TeX-searchable directory or keep them next to your document.

## Usage

~~~latex
\documentclass{article}
\usepackage{unipersian}

\begin{document}
سلام دنیا!
\end{document}
~~~

Compile with LuaLaTeX (recommended), XeLaTeX, or pdfTeX (basic support).

UniPersian accepts Babel options and provides the package-specific options `persianprovide`, `englishprovide`, and `encodings`. For Persian presentations, the `beamer-rl` class is recommended with LuaLaTeX; the standard `beamer` class is also supported.

## Documentation
See the [main manual](doc/unipersian.pdf) for configuration and examples.

## License and maintainer

- License: LPPL 1.3c or later.
- Maintainer: Amer Amikhteh.
- Report bugs at the [issue tracker](https://github.com/gambi-shah/unipersian/issues).
