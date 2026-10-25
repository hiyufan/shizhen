"""旧包名的兼容入口；业务实现与模块状态均由 qiuyuantu 提供。

只在旧包被导入时注册别名加载器，按需转发子模块。不能直接复用新包的搜索路径：
同一源码以两个名字加载会产生不同的模型类、任务队列、配置和限流状态。
"""

import sys
from importlib import import_module, util
from importlib.abc import Loader, MetaPathFinder

_PREFIX = __name__ + "."


class _AliasLoader(Loader):
    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        # 用真正的模块替换占位对象，避免导入器把新模块的 __spec__ 改成旧名字。
        sys.modules[module.__name__] = import_module(self.target)

    def get_code(self, fullname):
        # 保留 python -m parse_video_py.diag；普通导入仍复用同一个模块对象。
        spec = util.find_spec(self.target)
        return spec.loader.get_code(self.target)


class _AliasFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_PREFIX):
            return None
        canonical = "qiuyuantu." + fullname.removeprefix(_PREFIX)
        spec = util.find_spec(canonical)
        if spec is None:
            return None
        return util.spec_from_loader(
            fullname,
            _AliasLoader(canonical),
            is_package=spec.submodule_search_locations is not None,
        )


sys.meta_path.insert(0, _AliasFinder())
sys.modules[__name__] = import_module("qiuyuantu")
