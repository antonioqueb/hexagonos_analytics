"""Compile the manifest's SCSS using Odoo's LibSass, not Dart Sass.

Run with Odoo's Python environment (libsass already installed):
    python3 hexagonos_analytics/tests/check_styles.py [/tmp/hmx-qa/analytics.css]
This checks addon sources; it does not compile a deployed database's full bundle.
"""
import ast
import sys
from pathlib import Path

import sass


root = Path(__file__).resolve().parents[1]
manifest = ast.literal_eval((root / '__manifest__.py').read_text())
paths = [root.parent / name for name in manifest['assets']['web.assets_backend']
         if isinstance(name, str) and name.endswith('.scss')]
assert paths, 'No SCSS assets found in the backend manifest'
source = '\n'.join(path.read_text() for path in paths)
# Same output style and precision as Odoo 18 ScssStylesheetAsset.compile.
css = sass.compile(string=source, output_style='expanded', precision=8)
assert css.strip(), 'Compilation returned empty CSS'
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(css)
print(f'PASS: {len(paths)} SCSS asset(s), LibSass {sass.__version__}, {len(css)} CSS characters')
