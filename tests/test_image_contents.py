"""What the deployed image ships, read from the layers //:push publishes.

A test target assembles its own interpreter, so a test that merely imports a
package proves the *test* has it, not the image. That gap shipped an image
without cryptography: //:test_auth listed it, stayed green, and every sign-in
was refused with "Cloudflare published no usable signing keys". This reads the
py_image_layer tars behind //:image instead, so what it asserts is what the
container gets, whatever any test target's deps say.
"""

import os
import tarfile
from pathlib import Path

import pytest

# Packages the server needs at run time that arrive only through an extra or
# a transitive dependency, and so are easy to lose from the Bazel graph without
# any import in this repository's own code noticing.
REQUIRED_PACKAGES = [
    "jwt",
    # pyjwt's [crypto] extra. Without it PyJWT refuses every RS256 key.
    "cryptography",
    # uvicorn's [standard] extra (colorama is Windows-only). Losing these
    # fails nothing -- uvicorn falls back -- which is why nothing else would
    # notice. See :app_lib in BUILD.bazel for what each one changes.
    "dotenv",
    "httptools",
    "uvloop",
    "watchfiles",
    "websockets",
    "yaml",
]


@pytest.fixture(scope="module")
def site_packages() -> set[str]:
    tars = os.environ["SERVER_LAYERS"].split()
    assert tars, "SERVER_LAYERS names no layer tars"
    found = set()
    for name in tars:
        with tarfile.open(Path(name)) as tar:
            for member in tar.getnames():
                parts = Path(member).parts
                if "site-packages" in parts:
                    i = parts.index("site-packages")
                    if i + 1 < len(parts):
                        found.add(parts[i + 1])
    assert found, "no site-packages entries in the image layers"
    return found


@pytest.mark.parametrize("package", REQUIRED_PACKAGES)
def test_the_image_ships(site_packages, package):
    assert package in site_packages
