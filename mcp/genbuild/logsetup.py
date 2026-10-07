# -*- coding: utf-8 -*-
u"""genbuild.logsetup - logging 配置。

双通道:
  文件    DEBUG 级, 每行带时间戳 (完整命令响应也写文件, 便于事后审计)
  控制台  INFO 级, 纯消息 (与旧版 print 行为对齐)
日志文件落在 <配方目录>/logs/<名称>-<月日-时分秒>.log, 与旧版命名一致。
"""
import logging
import os
import time


def setup_logging(logdir, name):
    u"""初始化 genbuild logger, 返回 (logger, 日志文件路径)。

    可重入: 重复调用会先清空旧 handler (同一进程跑多次构建安全)。
    """
    if not os.path.isdir(logdir):
        os.makedirs(logdir)
    path = os.path.join(
        logdir, u"%s-%s.log" % (name, time.strftime(u"%m%d-%H%M%S")))

    logger = logging.getLogger(u"genbuild")
    logger.setLevel(logging.DEBUG)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    fh = logging.FileHandler(path, encoding=u"utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(u"%(asctime)s %(message)s",
                                      datefmt=u"%H:%M:%S"))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(u"%(message)s"))
    logger.addHandler(ch)
    return logger, path
