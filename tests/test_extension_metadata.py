import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "extension" / "META-INF" / "manifest.xml"
ADDONS = ROOT / "extension" / "Addons.xcu"
EXTENSION_DIR = ROOT / "extension"


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

    def test_toolbar_commands_are_icon_backed_and_merged_at_end(self):
        tree = ET.parse(ADDONS)
        ns = {"oor": "http://openoffice.org/2001/registry"}
        oor_name = f"{{{ns['oor']}}}name"

        addon_ui = tree.find(".//node[@oor:name='AddonUI']", ns)
        self.assertIsNotNone(addon_ui)
        self.assertIsNone(addon_ui.find("node[@oor:name='OfficeToolBar']", ns))

        image_urls = {
            (image.find("prop[@oor:name='URL']/value", ns).text or "")
            for image in tree.findall(".//node[@oor:name='Images']/node", ns)
        }
        self.assertTrue(
            {
                "vnd.librecompleteai:toggle",
                "vnd.librecompleteai:continuous",
                "vnd.librecompleteai:complete",
            }
            <= image_urls
        )

        for image in tree.findall(".//node[@oor:name='Images']/node", ns):
            self.assertTrue((image.find(".//prop[@oor:name='ImageSmall']/value", ns).text or "").strip())
            self.assertTrue((image.find(".//prop[@oor:name='ImageBig']/value", ns).text or "").strip())

        toolbar_items = tree.findall(".//node[@oor:name='OfficeToolbarMerging']//node[@oor:name='ToolBarItems']/node", ns)
        image_identifiers = {
            (item.find("prop[@oor:name='URL']/value", ns).text or ""): (
                item.find("prop[@oor:name='ImageIdentifier']/value", ns).text or ""
            )
            for item in toolbar_items
            if item.find("prop[@oor:name='ImageIdentifier']/value", ns) is not None
        }
        expected_identifiers = {
            "vnd.librecompleteai:toggle": "%origin%/images/toggle",
            "vnd.librecompleteai:continuous": "%origin%/images/continuous",
            "vnd.librecompleteai:complete": "%origin%/images/complete",
            "vnd.sun.star.script:LibreCompleteAI.oxt|Scripts|python|writer_autocomplete.py$show_settings?language=Python&location=user:uno_packages": "%origin%/images/settings",
        }
        self.assertEqual(image_identifiers, expected_identifiers)
        for identifier in expected_identifiers.values():
            icon_base = identifier[len("%origin%/") :]
            self.assertTrue((EXTENSION_DIR / f"{icon_base}_16.bmp").is_file())
            self.assertTrue((EXTENSION_DIR / f"{icon_base}_26.bmp").is_file())

        merge_command = tree.find(".//node[@oor:name='OfficeToolbarMerging']//prop[@oor:name='MergeCommand']/value", ns)
        self.assertIsNotNone(merge_command)
        self.assertEqual(merge_command.text, "AddLast")


if __name__ == "__main__":
    unittest.main()
