import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "extension" / "META-INF" / "manifest.xml"
DESCRIPTION = ROOT / "extension" / "description.xml"
ADDONS = ROOT / "extension" / "Addons.xcu"
WRITER_WINDOW_STATE = ROOT / "extension" / "WriterWindowState.xcu"
OPTIONS_DIALOG = ROOT / "extension" / "options" / "LibreCompleteAI.xdl"
EXTENSION_DIR = ROOT / "extension"


class ExtensionMetadataTests(unittest.TestCase):
    def test_options_dialog_exposes_ollama_completion_mode(self):
        tree = ET.parse(OPTIONS_DIALOG)
        ns = {"dlg": "http://openoffice.org/2000/dialog"}
        control = tree.find(".//*[@dlg:id='ollama_completion_mode']", ns)
        self.assertIsNotNone(control)
        self.assertEqual(control.attrib[f"{{{ns['dlg']}}}value"], "auto")
        self.assertIn("guided", control.attrib[f"{{{ns['dlg']}}}help-text"])

    def test_options_dialog_exposes_codex_provider_settings(self):
        tree = ET.parse(OPTIONS_DIALOG)
        ns = {"dlg": "http://openoffice.org/2000/dialog"}
        provider = tree.find(".//*[@dlg:id='provider']", ns)
        executable = tree.find(".//*[@dlg:id='codex_executable']", ns)
        model = tree.find(".//*[@dlg:id='codex_model']", ns)
        self.assertIn("codex", provider.attrib[f"{{{ns['dlg']}}}help-text"])
        self.assertEqual(executable.attrib[f"{{{ns['dlg']}}}value"], "codex")
        self.assertIn("full path", executable.attrib[f"{{{ns['dlg']}}}help-text"])
        self.assertIn("leave empty", model.attrib[f"{{{ns['dlg']}}}help-text"])

    def test_options_dialog_exposes_reference_file_controls(self):
        tree = ET.parse(OPTIONS_DIALOG)
        ns = {
            "dlg": "http://openoffice.org/2000/dialog",
            "script": "http://openoffice.org/2000/script",
        }
        paths = tree.find(".//*[@dlg:id='reference_files']", ns)
        add_button = tree.find(".//*[@dlg:id='add_reference_files']", ns)
        clear_button = tree.find(".//*[@dlg:id='clear_reference_files']", ns)
        self.assertIsNotNone(paths)
        self.assertEqual(paths.attrib[f"{{{ns['dlg']}}}multiline"], "true")
        self.assertIn(".md", paths.attrib[f"{{{ns['dlg']}}}help-text"])
        self.assertEqual(
            add_button.find("script:event", ns).attrib[f"{{{ns['script']}}}macro-name"],
            "vnd.sun.star.UNO:add_reference_files",
        )
        self.assertEqual(
            clear_button.find("script:event", ns).attrib[f"{{{ns['script']}}}macro-name"],
            "vnd.sun.star.UNO:clear_reference_files",
        )

    def test_extension_version_is_0_3_0(self):
        tree = ET.parse(DESCRIPTION)
        ns = {"desc": "http://openoffice.org/extensions/description/2006"}
        version = tree.find("desc:version", ns)
        self.assertEqual(version.attrib["value"], "0.3.0")

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
        self.assertTrue(
            {
                "vnd.librecompleteai:toggle",
                "vnd.librecompleteai:continuous",
                "vnd.librecompleteai:complete",
                "vnd.librecompleteai:settings",
            }
            <= protocol_commands
        )

    def test_toolbar_uses_short_text_buttons_without_blank_standardbar_merge(self):
        tree = ET.parse(ADDONS)
        ns = {"oor": "http://openoffice.org/2001/registry"}
        oor_name = f"{{{ns['oor']}}}name"

        addon_ui = tree.find(".//node[@oor:name='AddonUI']", ns)
        self.assertIsNotNone(addon_ui)
        self.assertIsNone(addon_ui.find("node[@oor:name='OfficeToolbarMerging']", ns))

        toolbar = addon_ui.find(
            "node[@oor:name='OfficeToolBar']/node[@oor:name='org.codex.librecompleteai.toolbar']",
            ns,
        )
        self.assertIsNotNone(toolbar)
        self.assertIsNone(toolbar.find("node[@oor:name='ToolBarItems']", ns))
        toolbar_items = toolbar.findall("node", ns)
        toolbar_commands = [
            (
                item.attrib[oor_name],
                item.find("prop[@oor:name='URL']/value", ns).text or "",
                item.find("prop[@oor:name='Title']/value", ns).text or "",
            )
            for item in toolbar_items
        ]
        self.assertEqual(
            toolbar_commands,
            [
                ("toggle", "vnd.librecompleteai:toggle", "LC-AI"),
                ("continuous", "vnd.librecompleteai:continuous", "Continuous"),
                ("complete", "vnd.librecompleteai:complete", "Complete"),
                ("settings", "vnd.librecompleteai:settings", "Settings"),
            ],
        )
        self.assertFalse(toolbar.findall(".//prop[@oor:name='ImageIdentifier']", ns))

        image_urls = {
            (image.find("prop[@oor:name='URL']/value", ns).text or "")
            for image in tree.findall(".//node[@oor:name='Images']/node", ns)
        }
        self.assertTrue(
            {
                "vnd.librecompleteai:toggle",
                "vnd.librecompleteai:continuous",
                "vnd.librecompleteai:complete",
                "vnd.librecompleteai:settings",
            }
            <= image_urls
        )

        for image in tree.findall(".//node[@oor:name='Images']/node", ns):
            self.assertTrue((image.find(".//prop[@oor:name='ImageSmall']/value", ns).text or "").strip())
            self.assertTrue((image.find(".//prop[@oor:name='ImageBig']/value", ns).text or "").strip())

        for icon_base in ("toggle", "continuous", "complete", "settings"):
            self.assertTrue((EXTENSION_DIR / f"images/{icon_base}_16.bmp").is_file())
            self.assertTrue((EXTENSION_DIR / f"images/{icon_base}_26.bmp").is_file())

    def test_toolbar_window_state_forces_text_button_style(self):
        tree = ET.parse(WRITER_WINDOW_STATE)
        ns = {"oor": "http://openoffice.org/2001/registry"}
        state = tree.find(".//node[@oor:name='private:resource/toolbar/addon_org.codex.librecompleteai.toolbar']", ns)
        self.assertIsNotNone(state)

        values = {
            prop.attrib[f"{{{ns['oor']}}}name"]: prop.find("value").text or ""
            for prop in state.findall("prop", ns)
        }
        self.assertEqual(values["Visible"], "true")
        self.assertEqual(values["Docked"], "true")
        self.assertEqual(values["DockingArea"], "0")
        self.assertEqual(values["Style"], "1")


if __name__ == "__main__":
    unittest.main()
