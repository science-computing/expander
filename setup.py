#!/usr/bin/env python3

""" setuptools module setup script """

import os.path
import setuptools

here = os.path.abspath(os.path.dirname(__file__))

# "import" __version__, __description__, __author__ and __license__ without
# running into dependency issues
__version__ = None
__description__ = None
__author__ = None
__license__ = None
with open(os.path.join(
        here, "expander", "__init__.py"), encoding="utf-8") as init:
    exec(init.read())

# Get the long description from the README file
with open(os.path.join(here, "README.md"), encoding="utf-8") as readme:
    long_description = readme.read()

# get the dependencies and installs
with open(os.path.join(
        here, "requirements.txt"), encoding="utf-8") as reqs:
    all_reqs = reqs.read().split("\n")

install_requires = []
for req in all_reqs:
    # for git repo references, extract the egg/package name from the end and
    # prepend it with @ as per pip Github issue 3939
    if "git+" in req:
        egg = req.split("=")[-1]
        req = f"{egg} @ {req}"

    install_requires.append(req.strip())

GITHUB_BASE = "https://github.com/science-computing/expander"
setuptools.setup(
    name="expander",
    version=__version__,
    description=__description__,
    long_description=long_description,
    long_description_content_type="text/markdown",
    url=f"{GITHUB_BASE}.git",
    download_url=f"{GITHUB_BASE}/archive/master.zip",
    license=__license__,
    classifiers=[
      "Development Status :: 4 - Beta",
      "Operating System :: POSIX",
      "Programming Language :: Python",
      "Programming Language :: Python :: 3",
      "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
      "Natural Language :: English",
      "Natural Language :: German",
      "Topic :: Communications :: Email :: Filters",
    ],
    keywords="Karton, Peekaboo, rspamd",
    packages=setuptools.find_packages(exclude=["docs", "tests*"]),
    include_package_data=True,
    # package_files augments MANIFEST.in in what is packaged into a
    # distribution. Files to add must be inside a package. Thus files in the
    # root of our source directory cannot be packaged with this. Files inside
    # packages will stay there, totally obscured to the user. Meant for default
    # configuration or other package-internal data.
    #  package_files=[...],
    #
    # data_files is another way to augment what is installed from the
    # distribution. Allows paths outside packages for both sources and targets.
    # Absolute paths are strongly discouraged because they exhibit confusing if
    # not downright broken behaviour. Relative paths are added to the
    # distribution and then installed into
    # site-packages/<package>-<ver>.egg/<relative path> (setup.py) or <python
    # prefix>/<relative path> (pip). The latter works well with venvs and
    # distribution-provided pip (e.g. /usr/local/<relative path>).
    # We go this route for providing sample files because using setup.py
    # directly is discouraged anyway and "only" tucks the files away in the egg
    # directory, providing the most consistent option.
    data_files=[
        ("share/doc/expander", [
            "README.md",
            "CHANGELOG.md",
            "docs/expander-logo.svg",
            "docs/expander-schematic.svg",
        ]),
    ],
    # overriding the whole install_data command allows for arbitrary
    # installation mechanics but does not solve the problem of adding files to
    # a binary distribution (e.g. wheel, which pip uses internally always) in
    # such a way that they will later be put at the correct location. Thus they
    # would go around pip entirely, be missing from any actual wheel
    # distribution package, pollute the system directly and not be removed upon
    # uninstall or upgrade.
    #  cmdclass={
    #      "install_data": OffsetDataInstall,
    #  },
    author=__author__,
    python_requires=">=3.8",
    install_requires=install_requires,
    author_email="michael.weiser@gmx.de",
    entry_points={
        "console_scripts": [
            "expander-api = expander.api:main",
            "expander-deduper = expander.deduper:ExpanderDeduper.main",
            "expander-cache-responder = "
                "expander.cache.responder:ExpanderCacheResponder.main",
            "expander-peekaboo-submitter = "
                "expander.peekaboo.submitter:ExpanderPeekabooSubmitter.main",
            "expander-poker = expander.poker:ExpanderPoker.main",
            "expander-peekaboo-tracker = "
                "expander.peekaboo.tracker:ExpanderPeekabooTracker.main",
            "expander-correlator = "
                "expander.correlator:ExpanderCorrelator.main",
        ],
    }
)
