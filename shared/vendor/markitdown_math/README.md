# MarkItDown OMML conversion subset

`omml.py` and `latex_dict.py` are unmodified copies from Microsoft MarkItDown,
commit `b8f79c57ebc0044be41323d89b2a45d3fda8460e` (inspected 2026-09-29):
https://github.com/microsoft/markitdown/tree/b8f79c57ebc0044be41323d89b2a45d3fda8460e/packages/markitdown/src/markitdown/converter_utils/docx/math

MarkItDown is MIT licensed; its headers credit adaptation from xiilei/dwml
(Apache 2.0). Both licenses are retained here. This small subset avoids pulling
unrelated PDF, audio, Markdown and LLM conversion dependencies into our build.

Our adapter `shared/docx_math.py` converts each equation independently from
OMML → LaTeX → static MathML (latex2mathml), strips non-MathML elements and
unsafe attributes, and reports any failure without silently discarding it.
Formulas are static MathML sanitized before insertion into the document Shadow DOM. WMF/MTEF/OLE equations
are a separate format and are not supported by this OMML converter.
