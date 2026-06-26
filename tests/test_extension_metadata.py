import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "extension" / "META-INF" / "manifest.xml"
ADDONS = ROOT / "extension" / "Addons.xcu"


class ExtensionMetadataTests(unittest.TestCase):
    def test_manifest_exports_python_scripts_to_script_provider(self):
        tree = ET.parse(MANIFEST)
        ns = {"manifest": "http://openoffice.org/2001/manifest"}
        entries = {
            entry.attrib[f"{{{ns['manifest']}}}full-path"]: entry.attrib[f"{{{ns['manifest']}}}media-type"]
            for entry in tree.findall("manifest:file-entry", ns)
        }
        self.assertEqual(
            entries["Scripts/python"],
            "application/vnd.sun.star.framework-script",
        )
        for config_file in ("ProtocolHandler.xcu", "WriterCommands.xcu", "WriterWindowState.xcu"):
            self.assertEqual(
                entries[config_file],
                "application/vnd.sun.star.configuration-data",
            )

    def test_addon_urls_include_installed_package_path(self):
        tree = ET.parse(ADDONS)
        ns = {"oor": "http://openoffice.org/2001/registry"}
        oor_name = f"{{{ns['oor']}}}name"
        values = [
            value.text or ""
            for prop in tree.findall(".//prop")
            if prop.attrib.get(oor_name) == "URL"
            for value in prop.findall("value")
        ]
        self.assertTrue(values)
        protocol_commands = set()
        for value in values:
            if value.startswith("vnd.sun.star.script:"):
                self.assertTrue(
                    value.startswith("vnd.sun.star.script:LibreCompleteAI.oxt|Scripts|python|"),
                    value,
                )
                self.assertIn("location=user:uno_packages", value)
            elif value.startswith("vnd.librecompleteai:"):
                protocol_commands.add(value)
            elif value == "private:separator":
                continue
            else:
                self.fail(value)
        self.assertTrue({"vnd.librecompleteai:toggle", "vnd.librecompleteai:continuous"} <= protocol_commands)


if __name__ == "__main__":
    unittest.main()
