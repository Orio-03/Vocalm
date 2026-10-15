"""Every picture, video and link in the README and on the website points at something that exists, and every
version has release notes. Runs with the other tests, and before the website is published."""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vocalm import __version__  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def local_targets(text):
    """Relative src/href targets and Markdown link/image targets, without anchors or queries."""
    found = re.findall(r'(?:src|href|poster)="([^"]+)"', text) + re.findall(r"\]\(([^)\s]+)\)", text)
    out = []
    for target in found:
        if re.match(r"^(https?:|mailto:|#|data:)", target):
            continue
        out.append(target.split("#")[0].split("?")[0])
    return [t for t in out if t]


class DocsTest(unittest.TestCase):
    def test_readme_links_and_pictures_exist(self):
        for target in local_targets(read("README.md")):
            self.assertTrue(os.path.exists(os.path.join(ROOT, target)), f"README.md points at missing {target}")

    def test_readme_anchors_exist(self):
        readme = read("README.md")
        anchors = set(re.findall(r'<a id="([^"]+)"', readme))
        anchors |= {re.sub(r"[^\w\- ]", "", h).strip().lower().replace(" ", "-")
                    for h in re.findall(r"^#+ (.+)$", readme, re.M)}
        for anchor in re.findall(r'\]\(#([^)]+)\)|href="#([^"]+)"', readme):
            name = anchor[0] or anchor[1]
            self.assertIn(name, anchors, f"README.md links to #{name}, which isn't there")

    def test_site_files_exist(self):
        # The site is published from site/ plus docs/images (as images/) and a few files from assets/
        where = {"images/": os.path.join("docs", "images"), "logo.svg": "assets", "logo-256.png": "assets",
                 "social-preview.png": "assets"}
        for target in local_targets(read("site", "index.html")):
            if target.startswith("images/"):
                path = os.path.join(ROOT, where["images/"], target[len("images/"):])
            elif target in where:
                path = os.path.join(ROOT, where[target], target)
            else:
                path = os.path.join(ROOT, "site", target)
            self.assertTrue(os.path.exists(path), f"site/index.html points at missing {target}")

    def test_this_version_has_release_notes(self):
        self.assertIn(f"## [{__version__}]", read("CHANGELOG.md"))


if __name__ == "__main__":
    unittest.main()
