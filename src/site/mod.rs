//! 站点页面：首页 / 落地页 / 教程 / 统计页 / 404，外加静态资源。
//!
//! 模板（web/templates）、静态资源（web/static）和文案（content/*.toml）都编译进
//! 二进制，部署只要一个文件加 ffmpeg。模板是 Jinja 语法，由 minijinja 渲染。

pub mod content;
pub mod seo;

use std::sync::LazyLock;

use minijinja::{context, Environment, Value};
use serde::Serialize;
use sha2::{Digest, Sha256};

pub use content::Content;

/// 模板里用到的站点配置。
#[derive(Debug, Clone, Default)]
pub struct SiteConfig {
    /// 配了就用它做 canonical 等绝对地址，否则按请求推断
    pub site_url: String,
    pub site_verification_html: String,
    pub analytics_html: String,
}

pub struct Site {
    env: Environment<'static>,
    pub content: Content,
    cfg: SiteConfig,
}

impl std::fmt::Debug for Site {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Site").finish_non_exhaustive()
    }
}

/// 每个页面都有的上下文。
#[derive(Serialize)]
struct Common<'a> {
    title: &'a str,
    description: &'a str,
    keywords: &'a str,
    pages: &'a [content::Page],
    canonical: String,
    og_image: String,
    json_ld: String,
    site_name: &'static str,
    site_verification: &'a str,
    analytics: &'a str,
    v: &'static str,
    /// 首页代码示例里的接口地址
    api_base: String,
}

/// 一个页面的标题、描述和路径。
struct Meta<'a> {
    title: &'a str,
    description: &'a str,
    keywords: &'a str,
    path: &'a str,
    json_ld: String,
}

pub type Rendered = Result<String, minijinja::Error>;

impl Site {
    pub fn new(content: Content, cfg: SiteConfig) -> Result<Self, minijinja::Error> {
        let mut env = Environment::new();
        env.set_formatter(escape_like_jinja2);
        for (name, source) in TEMPLATES {
            env.add_template(name, source)?;
        }
        Ok(Self { env, content, cfg })
    }

    /// 页面里的绝对地址用哪个根：配置优先，否则用请求的 Host。
    pub fn base_url(&self, request_base: &str) -> String {
        if self.cfg.site_url.is_empty() {
            request_base.trim_end_matches('/').to_owned()
        } else {
            self.cfg.site_url.clone()
        }
    }

    /// 首页和各落地页（同一个工具，不同的标题和文案）。
    pub fn landing(&self, slug: &str, base: &str) -> Option<Rendered> {
        let page = self.content.page(slug)?;
        let meta = Meta {
            title: &page.title,
            description: &page.description,
            keywords: &page.keywords,
            path: &page.path,
            json_ld: seo::page_ld(page, base),
        };
        let related = self.content.guides_by_slug(&page.guides);
        let ctx = context! { page => page, faq => &page.all_faq, related_guides => related };
        Some(self.render("index.html", &meta, base, ctx))
    }

    pub fn guides(&self, base: &str) -> Rendered {
        let json_ld = seo::dump(&serde_json::json!([seo::crumbs(
            base,
            &[(seo::SITE_NAME, "/"), ("教程", "/guides")]
        )]));
        let meta = Meta {
            title: "教程：抖音小红书图片实况保存、视频转 GIF 和实况照片 - 拾帧",
            description: "拾帧教程：小红书实况图保存到 iPhone、抖音图集原图下载、视频转实况照片、视频转 GIF、YouTube 1080p 下载，每篇两分钟照着做。",
            keywords: "小红书实况图保存,抖音图集下载,视频转实况照片,视频转gif教程",
            path: "/guides",
            json_ld,
        };
        let ctx = context! { page => self.content.home(), guides => &self.content.guides, guides_nav => true };
        self.render("guides.html", &meta, base, ctx)
    }

    pub fn guide(&self, slug: &str, base: &str) -> Option<Rendered> {
        let guide = self.content.guide(slug)?;
        let title = format!("{} - 拾帧", guide.title);
        let meta = Meta {
            title: &title,
            description: &guide.description,
            keywords: &guide.keywords,
            path: &guide.path,
            json_ld: seo::guide_ld(guide, base),
        };
        let tool = self
            .content
            .page(&guide.tool)
            .unwrap_or_else(|| self.content.home());
        let ctx = context! {
            page => tool,
            guide => guide,
            tool_path => &tool.path,
            related => self.content.guides_by_slug(&guide.related),
            guides_nav => true,
        };
        Some(self.render("guide.html", &meta, base, ctx))
    }

    pub fn not_found(&self, path: &str, base: &str) -> Rendered {
        let meta = Meta {
            title: "页面不存在 - 拾帧",
            description: "这一页不存在。",
            keywords: "",
            path,
            json_ld: String::new(),
        };
        self.render(
            "404.html",
            &meta,
            base,
            context! { page => self.content.home() },
        )
    }

    pub fn stats(&self, token: &str, ranges: &[&str], base: &str) -> Rendered {
        let meta = Meta {
            title: "使用统计 - 拾帧",
            description: "",
            keywords: "",
            path: "/stats",
            json_ld: String::new(),
        };
        let ctx = context! { page => self.content.home(), ranges => ranges, token => token };
        self.render("stats.html", &meta, base, ctx)
    }

    fn render(&self, template: &str, meta: &Meta<'_>, base: &str, extra: Value) -> Rendered {
        let common = Common {
            title: meta.title,
            description: meta.description,
            keywords: meta.keywords,
            pages: &self.content.pages,
            canonical: seo::absolute(base, meta.path),
            og_image: seo::absolute(base, "/static/og.png"),
            json_ld: meta.json_ld.clone(),
            site_name: seo::SITE_NAME,
            site_verification: &self.cfg.site_verification_html,
            analytics: &self.cfg.analytics_html,
            v: asset_version(),
            api_base: base.trim_end_matches('/').to_owned(),
        };
        let ctx = context! { ..extra, ..Value::from_serialize(&common) };
        self.env.get_template(template)?.render(ctx)
    }
}

/// 和 Jinja2（markupsafe）一样只转义 `& < > " '`。
///
/// minijinja 默认还会把 `/` 转成 `&#x2f;`，浏览器照样认，但 canonical 之类的地址
/// 会和 Python 版的输出不一样，搜索引擎看到的页面平白变了。
fn escape_like_jinja2(
    out: &mut minijinja::Output<'_>,
    state: &minijinja::State<'_, '_>,
    value: &Value,
) -> Result<(), minijinja::Error> {
    if value.is_safe() || state.auto_escape() == minijinja::AutoEscape::None {
        return minijinja::escape_formatter(out, state, value);
    }
    let text = value.to_string();
    let mut escaped = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&#34;"),
            '\'' => escaped.push_str("&#39;"),
            c => escaped.push(c),
        }
    }
    out.write_str(&escaped)
        .map_err(|e| minijinja::Error::new(minijinja::ErrorKind::WriteFailure, e.to_string()))
}

const TEMPLATES: &[(&str, &str)] = &[
    ("base.html", include_str!("../../web/templates/base.html")),
    (
        "_footer.html",
        include_str!("../../web/templates/_footer.html"),
    ),
    ("index.html", include_str!("../../web/templates/index.html")),
    (
        "guides.html",
        include_str!("../../web/templates/guides.html"),
    ),
    ("guide.html", include_str!("../../web/templates/guide.html")),
    ("404.html", include_str!("../../web/templates/404.html")),
    ("stats.html", include_str!("../../web/templates/stats.html")),
];

/// 静态资源：(文件名, 内容, Content-Type)。新增文件在这里加一行。
const STATIC: &[(&str, &[u8], &str)] = &[
    (
        "fonts.css",
        include_bytes!("../../web/static/fonts.css"),
        "text/css; charset=utf-8",
    ),
    (
        "site.css",
        include_bytes!("../../web/static/site.css"),
        "text/css; charset=utf-8",
    ),
    (
        "og.png",
        include_bytes!("../../web/static/og.png"),
        "image/png",
    ),
    (
        "inter.woff2",
        include_bytes!("../../web/static/inter.woff2"),
        "font/woff2",
    ),
    (
        "jetbrains-mono.woff2",
        include_bytes!("../../web/static/jetbrains-mono.woff2"),
        "font/woff2",
    ),
    (
        "noto-serif-sc-700.woff2",
        include_bytes!("../../web/static/noto-serif-sc-700.woff2"),
        "font/woff2",
    ),
    (
        "noto-serif-sc-900.woff2",
        include_bytes!("../../web/static/noto-serif-sc-900.woff2"),
        "font/woff2",
    ),
    (
        "playfair-italic.woff2",
        include_bytes!("../../web/static/playfair-italic.woff2"),
        "font/woff2",
    ),
];

/// 取一个静态资源：(内容, Content-Type)。
pub fn static_file(name: &str) -> Option<(&'static [u8], &'static str)> {
    STATIC
        .iter()
        .find(|(n, _, _)| *n == name)
        .map(|(_, body, ctype)| (*body, *ctype))
}

/// 静态资源版本号（按内容哈希）：改了字体 / 样式浏览器不会用旧缓存。
pub fn asset_version() -> &'static str {
    static V: LazyLock<String> = LazyLock::new(|| {
        let mut h = Sha256::new();
        for (name, body, _) in STATIC {
            h.update(name.as_bytes());
            h.update(body);
        }
        hex::encode(&h.finalize()[..4])
    });
    &V
}

#[cfg(test)]
mod tests {
    use super::*;

    fn site() -> Site {
        Site::new(Content::embedded().unwrap(), SiteConfig::default()).unwrap()
    }

    #[test]
    fn every_page_renders() {
        let s = site();
        for p in &s.content.pages {
            let html = s.landing(&p.slug, "https://x.com").unwrap().unwrap();
            assert!(
                html.contains(&format!("<title>{}</title>", p.title)),
                "{}",
                p.slug
            );
            assert!(
                html.contains("https://x.com/api/parse"),
                "代码示例里的接口地址: {}",
                p.slug
            );
            assert!(!html.contains("&#x2f;"), "斜杠不转义，和 Jinja2 一致");
        }
        for g in &s.content.guides {
            let html = s.guide(&g.slug, "https://x.com").unwrap().unwrap();
            assert!(html.contains(&g.h1));
        }
        assert!(s.guides("https://x.com").unwrap().contains("/guide/"));
        assert!(s
            .not_found("/nope", "https://x.com")
            .unwrap()
            .contains("<html"));
        assert!(s
            .stats("tok", &["24h", "7d"], "https://x.com")
            .unwrap()
            .contains("\"tok\""));
        assert!(s.landing("nope", "https://x.com").is_none());
    }

    #[test]
    fn html_is_escaped_but_trusted_fields_are_not() {
        let s = site();
        let html = s.landing("", "https://x.com").unwrap().unwrap();
        assert!(html.contains("<em>GIF</em>"), "h1 允许 <em>");
    }

    #[test]
    fn static_files_and_version() {
        assert_eq!(
            static_file("site.css").unwrap().1,
            "text/css; charset=utf-8"
        );
        assert!(static_file("../Cargo.toml").is_none());
        assert_eq!(asset_version().len(), 8);
    }
}
