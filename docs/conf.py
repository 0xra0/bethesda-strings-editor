import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

project = "Bethesda Strings Editor"
copyright = "2024, 0xra0"
author = "0xra0"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

myst_enable_extensions = ["colon_fence", "deflist"]

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
}
html_static_path = ["_static"]

templates_path = ["_templates"]
exclude_patterns = ["_build"]

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = False
