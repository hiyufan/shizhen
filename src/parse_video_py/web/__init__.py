"""网站和 HTTP 接口。

app.py 负责组装；各组路由按职责分文件：
- pages.py     首页、教程、SEO 落地页、robots / sitemap
- parse.py     /api/parse 解析、/api/feedback 失败反馈
- proxy.py     /api/proxy 转发第三方直链
- media.py     原视频准备 / 上传、转 GIF / 实况、服务端下载、任务查询
- admin.py     健康检查、使用统计、测试模式
- upstream.py  上游 parse-video-py 的老接口
"""

from .app import create_app

app = create_app()

__all__ = ["app", "create_app"]
