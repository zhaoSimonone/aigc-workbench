import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("organize", Path(__file__).resolve().parents[1] / "scripts/organize_assets.py")
organize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(organize)


class ApplyTests(unittest.TestCase):
    def test_direct_preview_saves_snapshot_and_rejects_later_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            client = Mock()
            client.metadata_policy.return_value = {"folders": organize.FOLDERS}
            client.get_asset.return_value = {"id": "one", "name": "原名称", "tags": ["a", "b", "c"]}
            args = argparse.Namespace(plan=None, set_name="新名称", set_tags=None, reason="用户纠正",
                                      asset_id=["one"], save_plan=str(Path(temp) / "plan.json"), confirm=False)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(organize.apply_command(client, args), 0)
            client.organize.assert_not_called()
            plan = json.loads(Path(args.save_plan).read_text())
            self.assertEqual(plan["items"][0]["before"], {"name": "原名称"})
            args.plan, args.save_plan, args.confirm = args.save_plan, None, True
            client.get_asset.return_value = {"id": "one", "name": "他人修改", "tags": ["a", "b", "c"]}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(organize.apply_command(client, args), 1)
            client.organize.assert_not_called()

    def test_tag_order_does_not_create_change(self):
        self.assertEqual(organize.changed_fields({"tags": ["a", "c", "b"]}, {"tags": ["c", "b", "a"]}), {})
