# -*- coding: utf-8 -*-
u"""genbuild 纯函数单元测试 (不依赖 TCP 桥 / Revit)。

运行:
  cd E:\\bim-toolkit\\mcp
  <venv-python> -m unittest tests.test_pure -v
"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from genbuild import config, spec                      # noqa: E402
from genbuild.bridge import esc, unwrap                # noqa: E402
from genbuild.engine import RunContext                 # noqa: E402
from genbuild.errors import SpecError                  # noqa: E402


class TestEsc(unittest.TestCase):
    def test_ascii_passthrough(self):
        self.assertEqual(esc(u"abc XYZ 123"), u"abc XYZ 123")

    def test_cjk_escaped(self):
        self.assertEqual(esc(u"墙"), u"\\u5899")

    def test_mixed(self):
        self.assertEqual(esc(u"w250墙"), u"w250\\u5899")

    def test_roundtrip_via_json(self):
        import json
        self.assertEqual(json.loads(u'"%s"' % esc(u"标高1")), u"标高1")


class TestUnwrap(unittest.TestCase):
    def test_plain_dict(self):
        d = {u"a": 1}
        self.assertEqual(unwrap(d), d)

    def test_single_wrap(self):
        self.assertEqual(unwrap({u"result": {u"a": 1}, u"status": u"ok"}),
                         {u"a": 1})

    def test_double_wrap(self):
        self.assertEqual(
            unwrap({u"result": {u"result": {u"a": 1}, u"status": u"ok"},
                    u"status": u"ok"}),
            {u"a": 1})

    def test_none_result_keeps_outer(self):
        self.assertEqual(unwrap({u"result": None, u"status": u"ok"}),
                         {u"result": None, u"status": u"ok"})


class TestStairGeometry(unittest.TestCase):
    def test_default_dir_plus_y(self):
        g = spec.stair_geometry({u"from": u"F1", u"to": u"F2",
                                 u"at": [100, 200]})
        self.assertEqual(g[u"run1"], [700, 200, 700, 2000])
        self.assertEqual(g[u"landing"][0], [100, 2000])

    def test_dir_plus_x_rotation(self):
        g = spec.stair_geometry({u"from": u"F1", u"to": u"F2",
                                 u"at": [0, 0], u"dir": [1, 0]})
        # rot(x,y)->(y,-x): run1 (600,0)-(600,1800) -> (0,-600)-(1800,-600)
        self.assertEqual(g[u"run1"], [0, -600, 1800, -600])

    def test_dir_minus_y(self):
        g = spec.stair_geometry({u"from": u"F1", u"to": u"F2",
                                 u"at": [50, 50], u"dir": [0, -1]})
        # rot(x,y)->(-x,-y): run1 (600,0)-(600,1800) ->
        # (-600,0)-(-600,-1800), 平移 at(50,50)
        self.assertEqual(g[u"run1"], [-550, 50, -550, -1750])

    def test_invalid_dir_raises(self):
        self.assertRaises(ValueError, spec.stair_geometry,
                          {u"from": u"F1", u"to": u"F2", u"at": [0, 0],
                           u"dir": [1, 1]})


class TestWallSegs(unittest.TestCase):
    def test_expansion_and_defaults(self):
        cfg = {u"walls": [
            {u"level": u"L1", u"t": 250, u"segs": [[0, 0, 1, 1]]},
            {u"level": u"L2", u"segs": [[2, 2, 3, 3],
                                        [4, 4, 5, 5]]}]}
        segs = spec.wall_segs(cfg)
        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[0][u"t"], 250)
        self.assertEqual(segs[1][u"t"], 100)                      # 默认厚
        self.assertEqual(segs[1][u"h"], config.WALL_H_DEFAULT)    # 默认高


def _minimal_cfg():
    return {
        u"schema": 1,
        u"name": u"t",
        u"levels": [{u"name": u"L1", u"elev": 0},
                    {u"name": u"L2", u"elev": 3000}],
        u"walls": [{u"level": u"L1", u"t": 240, u"segs":
                    [[0, 0, 6000, 0], [6000, 0, 6000, 4000],
                     [6000, 4000, 0, 4000], [0, 4000, 0, 0]]}],
        u"openings": [{u"kind": u"window", u"level": u"L1",
                       u"at": [3000, 0], u"w": 1500}],
    }


class TestValidate(unittest.TestCase):
    def test_minimal_ok(self):
        probs, warns, stats = spec.validate(_minimal_cfg())
        self.assertEqual(probs, [])
        self.assertEqual(stats[u"openings_checked"], 1)

    def test_missing_schema(self):
        cfg = _minimal_cfg()
        del cfg[u"schema"]
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"schema" in p for p in probs))

    def test_duplicate_levels(self):
        cfg = _minimal_cfg()
        cfg[u"levels"].append({u"name": u"L1", u"elev": 0})
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"重复" in p for p in probs))

    def test_undefined_level_ref(self):
        cfg = _minimal_cfg()
        cfg[u"floors"] = [{u"level": u"L9", u"points": [[0, 0], [1, 0],
                                                        [0, 1]]}]
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"未定义标高" in p for p in probs))

    def test_floor_needs_3_points(self):
        cfg = _minimal_cfg()
        cfg[u"floors"] = [{u"level": u"L1", u"points": [[0, 0], [1, 0]]}]
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"至少 3 点" in p for p in probs))

    def test_opening_no_wall_nearby(self):
        cfg = _minimal_cfg()
        cfg[u"openings"] = [{u"kind": u"window", u"level": u"L1",
                             u"at": [3000, 9000], u"w": 1500}]
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"无同标高墙" in p for p in probs))

    def test_opening_overflow(self):
        cfg = _minimal_cfg()
        cfg[u"openings"] = [{u"kind": u"window", u"level": u"L1",
                             u"at": [100, 0], u"w": 1500}]
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"越界" in p for p in probs))

    def test_opening_jamb_warning(self):
        cfg = _minimal_cfg()
        # 端距 760 ∈ [w/2-1, w/2+50) = [749, 800) -> 贴边警告
        cfg[u"openings"] = [{u"kind": u"window", u"level": u"L1",
                             u"at": [760, 0], u"w": 1500}]
        probs, warns, _ = spec.validate(cfg)
        self.assertEqual(probs, [])
        self.assertTrue(any(u"贴边" in w for w in warns))

    def test_bad_opening_kind_is_prob(self):
        cfg = _minimal_cfg()
        cfg[u"openings"] = [{u"kind": u"windowx", u"level": u"L1",
                             u"at": [3000, 0], u"w": 1500}]
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"kind" in p for p in probs))

    def test_roof_list_validation(self):
        cfg = _minimal_cfg()
        cfg[u"roofs"] = [{u"level": u"L2", u"span": [0, 6000],
                          u"cross": [0, 4000], u"macro": u"freeform"}]
        probs, _, _ = spec.validate(cfg)
        self.assertEqual(probs, [])
        cfg[u"roofs"][0][u"span"] = [0, 6000, 9]
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"span" in p for p in probs))

    def test_bad_axis_is_prob(self):
        cfg = _minimal_cfg()
        cfg[u"roof"] = {u"level": u"L2", u"axis": u"z",
                        u"span": [0, 1], u"cross": [0, 1]}
        probs, _, _ = spec.validate(cfg)
        self.assertTrue(any(u"axis" in p for p in probs))

    def test_unknown_expect_key_warns_only(self):
        cfg = _minimal_cfg()
        cfg[u"expect"] = {u"walls": 4, u"railingsx": 2}
        probs, warns, _ = spec.validate(cfg)
        self.assertEqual(probs, [])
        self.assertTrue(any(u"未知键" in w for w in warns))

    def test_dwg_adds_warning(self):
        cfg = _minimal_cfg()
        cfg[u"dwg"] = {u"json_path": u"no-such-file.json"}
        probs, warns, _ = spec.validate(cfg)
        self.assertEqual(probs, [])
        self.assertTrue(any(u"dwg" in w for w in warns))


def _cleanup(path):
    u"""尽力删除临时文件 (本机环境句柄延迟释放时忽略清理失败)。"""
    try:
        os.remove(path)
    except OSError:
        pass


class TestLoadSpec(unittest.TestCase):
    def test_load_ok(self):
        fd, path = tempfile.mkstemp(suffix=u".yaml")
        try:
            with os.fdopen(fd, u"w", encoding=u"utf-8") as f:
                f.write(u"schema: 1\nname: t\nlevels:\n")
            cfg = spec.load_spec(path)
            self.assertEqual(cfg[u"schema"], 1)
        finally:
            _cleanup(path)

    def test_bad_yaml_raises_spec_error(self):
        fd, path = tempfile.mkstemp(suffix=u".yaml")
        try:
            with os.fdopen(fd, u"w", encoding=u"utf-8") as f:
                f.write(u"a: 1\n  b: 2\n")   # 缩进错误
            self.assertRaises(SpecError, spec.load_spec, path)
        finally:
            _cleanup(path)

    def test_top_level_must_be_mapping(self):
        fd, path = tempfile.mkstemp(suffix=u".yaml")
        try:
            with os.fdopen(fd, u"w", encoding=u"utf-8") as f:
                f.write(u"- 1\n- 2\n")
            self.assertRaises(SpecError, spec.load_spec, path)
        finally:
            _cleanup(path)


class TestRunContext(unittest.TestCase):
    def test_ids_share_module_counter(self):
        from genbuild import engine
        ctx = RunContext(logger=None)
        self.assertIs(ctx._ids, engine.ID)
        before = engine.ID[0]
        ctx.next_id()
        self.assertEqual(engine.ID[0], before + 1)

    def test_material_cache_starts_empty(self):
        ctx = RunContext(logger=None)
        self.assertIsNone(ctx.material_avail)


if __name__ == u"__main__":
    unittest.main()
