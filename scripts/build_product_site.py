"""Build the product website after independently reproducing the research archive."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build' / 'public')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == (ROOT / 'site').resolve():
        parser.error('Use a build directory: site/ contains the product source assets.')
    subprocess.run([sys.executable, str(ROOT / 'reproduce.py'), '--output', str(output)], cwd=ROOT, check=True)
    shutil.copy2(output / 'index.html', output / 'research.html')
    shutil.copy2(ROOT / 'site' / 'index.html', output / 'index.html')
    shutil.copytree(ROOT / 'site' / 'workbench', output / 'workbench', dirs_exist_ok=True)
    shutil.copytree(ROOT / 'site' / 'terminal', output / 'terminal', dirs_exist_ok=True)
    subprocess.run([sys.executable, '-m', 'research_workbench.generate_demo', '--output', str(output / 'workbench' / 'data' / 'demo_reports.json')], cwd=ROOT, check=True)
    (output / '.nojekyll').touch()
    from audit.validate_outputs import validate_local_links
    validate_local_links(output, output / 'index.html')
    validate_local_links(output, output / 'research.html')
    validate_local_links(output, output / 'terminal' / 'index.html')
    print(f'Product homepage, workbench and reproduced research archive: {output}')


if __name__ == '__main__':
    main()
