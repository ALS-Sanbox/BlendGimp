import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONNECTION = ROOT / "blender" / "blendgimp" / "ipc" / "connection.py"
MAIN_PANEL = ROOT / "blender" / "blendgimp" / "ui" / "main_panel.py"
GIMP_PLUGIN = ROOT / "gimp" / "plugin" / "blendgimp" / "blendgimp.py"


def load_connection_module():
    spec = importlib.util.spec_from_file_location(
        "blendgimp_phase5_2_connection",
        CONNECTION,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubConnection:
    def __init__(self, response):
        self.response = response
        self.request_counter = 0

    def _next_request_id(self, prefix="request"):
        self.request_counter += 1
        return f"test-{prefix}-{self.request_counter}"

    def request(self, message, timeout=None, quiet=False):
        self.last_message = message
        self.last_timeout = timeout
        return dict(self.response)


class PhaseFiveTwoContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection_module = load_connection_module()
        cls.panel_text = MAIN_PANEL.read_text(encoding="utf-8")
        cls.plugin_text = GIMP_PLUGIN.read_text(encoding="utf-8")

    def _connection(self, response):
        stub = StubConnection(response)
        stub.create_image = (
            self.connection_module.BlendGimpConnection.create_image.__get__(
                stub,
                StubConnection,
            )
        )
        return stub

    def test_create_image_protocol_returns_runtime_ids(self):
        connection = self._connection(
            {
                "type": "IMAGE_CREATED",
                "ok": True,
                "image_id": 9,
                "layer_id": 12,
                "width": 2048,
                "height": 1024,
            }
        )

        response = connection.create_image(
            "BaseColor",
            2048,
            1024,
            image_format="RGBA",
            background="SOLID",
            background_color=(0.25, 0.5, 0.75, 1.0),
            layer_name="BaseColor",
        )

        self.assertEqual(response["image_id"], 9)
        self.assertEqual(response["layer_id"], 12)
        self.assertEqual(connection.last_message["type"], "CREATE_IMAGE")
        self.assertEqual(connection.last_message["format"], "RGBA")
        self.assertEqual(connection.last_message["background"], "SOLID")
        self.assertEqual(
            connection.last_timeout,
            self.connection_module.CREATE_IMAGE_TIMEOUT,
        )

    def test_rgb_rejects_transparent_background(self):
        connection = self._connection({})

        with self.assertRaises(ValueError):
            connection.create_image(
                "BaseColor",
                1024,
                1024,
                image_format="RGB",
                background="TRANSPARENT",
            )

    def test_gimp_creates_and_initializes_image_and_layer(self):
        for contract in (
            'if message_type == "CREATE_IMAGE"',
            '"type": "IMAGE_CREATED"',
            "Gimp.Image.new(",
            "Gimp.Layer.new(",
            "layer.fill(Gimp.FillType.TRANSPARENT)",
            "layer.fill(Gimp.FillType.FOREGROUND)",
            "image.insert_layer(layer, None, 0)",
            "image.set_selected_layers([layer])",
            '"blender_owned": True',
        ):
            self.assertIn(contract, self.plugin_text)

    def test_blender_ui_owns_pairing_and_starts_auto_sync(self):
        for contract in (
            'bl_idname = "blendgimp.create_image"',
            '"blendgimp_create_width"',
            '"blendgimp_create_height"',
            '"blendgimp_create_format"',
            '"blendgimp_create_background"',
            '"blendgimp_create_background_color"',
            '"blendgimp_create_layer_name"',
            'blender_image["blendgimp_created_by_blender"] = True',
            'blender_image["blendgimp_gimp_layer_id"] = layer_id',
            "scene.blendgimp_auto_sync_enabled = True",
            "reset_auto_sync_runtime(",
        ):
            self.assertIn(contract, self.panel_text)

    def test_session_token_prevents_runtime_id_collision(self):
        start = self.panel_text.index("def _find_blendgimp_image(")
        end = self.panel_text.index(
            "def get_or_update_blender_image_from_pixels(",
            start,
        )
        finder = self.panel_text[start:end]

        self.assertIn("if sync_token:", finder)
        self.assertIn("Ignoring stale Blender Image pairing", finder)
        self.assertIn("return None", finder)
        self.assertIn("Legacy compatibility path", finder)
        self.assertLess(
            finder.index("return None"),
            finder.index("Legacy compatibility path"),
        )

    def test_blender_rgba_buffer_contract_is_checked(self):
        self.assertIn(
            "expected_components = width * height * 4",
            self.panel_text,
        )
        self.assertIn(
            "Blender Image pixel buffer mismatch.",
            self.panel_text,
        )

    def test_legacy_xcf_is_not_reintroduced(self):
        for source_path in ROOT.rglob("*.xcf"):
            self.fail(f"Legacy XCF fixture was reintroduced: {source_path}")


if __name__ == "__main__":
    unittest.main()
