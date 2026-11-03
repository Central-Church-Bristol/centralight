"""CentraLight tests. Run them from the CentraLight folder:

    .venv\\Scripts\\python.exe -m unittest discover -s tests -t .

Importing this package installs tests/guard.py first, so no test can open a real COM
port, send Art-Net, or touch your real settings and shows.
"""

from tests import guard

guard.install()
