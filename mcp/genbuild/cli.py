# -*- coding: utf-8 -*-
u"""genbuild.cli - 命令行入口 (argparse)。

用法:
  python gen_build.py <building.yaml> [--dry]

退出码 (genbuild.errors):
  0 成功 / 1 配方错误 / 2 桥错误 / 3 构建错误 / 4 验收未达标
"""
import argparse
import json
import logging
import os
import sys

from genbuild import engine, logsetup, spec
from genbuild.errors import (EXIT_OK, EXIT_SPEC_ERROR, EXIT_BRIDGE_ERROR,
                             EXIT_BUILD_ERROR)


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog=u"gen_build",
        description=u"配置驱动 Revit 建模引擎 (桥冻结 7.10.4, "
                    u"配方全走 execute_code)")
    p.add_argument(u"yaml_path", help=u"配方 YAML 路径")
    p.add_argument(u"--dry", action=u"store_true",
                   help=u"只静态校验 + ping 桥, 不建模")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    try:
        cfg = spec.load_spec(args.yaml_path)
    except Exception as ex:  # SpecError 及其它载入问题
        print(u"[规格] %s" % ex)
        return EXIT_SPEC_ERROR

    name = cfg.get(u"name", u"unnamed")
    logdir = os.path.join(
        os.path.dirname(os.path.abspath(args.yaml_path)), u"logs")
    logger, logpath = logsetup.setup_logging(logdir, name)

    try:
        probs, warns, stats = spec.validate(cfg)
        logger.info(u"[校验] %s" % json.dumps(stats, ensure_ascii=False))
        for w in warns:
            logger.info(u"[警告] %s" % w)
        for p in probs:
            logger.info(u"[问题] %s" % p)
        if probs:
            logger.info(u"[中止] 干跑发现 %d 个问题, 不执行" % len(probs))
            return EXIT_SPEC_ERROR
        logger.info(u"[干跑] 通过 (0 问题)")

        ctx = engine.RunContext(logger=logger)
        return engine.execute(ctx, cfg, dry=args.dry)
    except Exception as ex:
        from genbuild.errors import BridgeError, BuildError
        if isinstance(ex, BridgeError):
            logger.error(u"[桥] %s" % ex)
            return EXIT_BRIDGE_ERROR
        if isinstance(ex, BuildError):
            logger.error(u"[构建] %s" % ex)
            return EXIT_BUILD_ERROR
        logger.exception(u"[未预期异常] %s" % ex)
        return EXIT_BUILD_ERROR
    finally:
        if logging.getLogger(u"genbuild").handlers:
            logger.info(u"[日志] %s" % logpath)
            logging.shutdown()


if __name__ == u"__main__":
    sys.exit(main())
