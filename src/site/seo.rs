//! 结构化数据（JSON-LD）、sitemap 和 robots.txt。

use serde_json::{json, Value};

use super::content::{Content, Guide, Page};

pub const SITE_NAME: &str = "拾帧";

/// 站点根地址 + 路径。
pub fn absolute(base: &str, path: &str) -> String {
    format!("{}{path}", base.trim_end_matches('/'))
}

/// 放进 `<script type="application/ld+json">`：`<` 要转义，否则 `</script>` 会提前闭合。
pub fn dump(value: &Value) -> String {
    value.to_string().replace('<', "\\u003c")
}

fn faq_ld(faq: &[(String, String)]) -> Value {
    let items: Vec<Value> = faq
        .iter()
        .map(|(q, a)| json!({"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}))
        .collect();
    json!({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": items})
}

pub fn crumbs(base: &str, items: &[(&str, &str)]) -> Value {
    let list: Vec<Value> = items
        .iter()
        .enumerate()
        .map(|(i, (name, path))| {
            json!({"@type": "ListItem", "position": i + 1, "name": name, "item": absolute(base, path)})
        })
        .collect();
    json!({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": list})
}

pub fn page_ld(page: &Page, base: &str) -> String {
    let url = absolute(base, &page.path);
    let app = json!({
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": SITE_NAME,
        "url": url,
        "description": page.description,
        "applicationCategory": "MultimediaApplication",
        "operatingSystem": "Any",
        "browserRequirements": "Requires JavaScript",
        "inLanguage": "zh-CN",
        "isAccessibleForFree": true,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CNY"},
        "featureList": ["无水印视频提取", "原图提取", "视频转 GIF", "视频转实况照片", "动态照片"],
    });
    let third = if page.slug.is_empty() {
        json!({"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
               "url": absolute(base, "/"), "inLanguage": "zh-CN"})
    } else {
        crumbs(base, &[(SITE_NAME, "/"), (&page.nav_label, &page.path)])
    };
    dump(&json!([app, faq_ld(&page.all_faq), third]))
}

pub fn guide_ld(guide: &Guide, base: &str) -> String {
    let url = absolute(base, &guide.path);
    let steps: Vec<Value> = guide
        .steps
        .iter()
        .enumerate()
        .map(|(i, (name, text))| {
            json!({"@type": "HowToStep", "position": i + 1, "name": name, "text": text,
                   "url": format!("{url}#step-{}", i + 1)})
        })
        .collect();
    let how_to = json!({
        "@context": "https://schema.org", "@type": "HowTo", "name": guide.h1,
        "description": guide.description, "inLanguage": "zh-CN", "totalTime": "PT3M",
        "tool": [{"@type": "HowToTool", "name": SITE_NAME}], "step": steps,
    });
    let article = json!({
        "@context": "https://schema.org", "@type": "Article", "headline": guide.title,
        "description": guide.description, "inLanguage": "zh-CN",
        "datePublished": guide.updated, "dateModified": guide.updated,
        "author": {"@type": "Organization", "name": SITE_NAME, "url": absolute(base, "/")},
        "publisher": {"@type": "Organization", "name": SITE_NAME},
        "mainEntityOfPage": url,
    });
    let trail = crumbs(base, &[(SITE_NAME, "/"), ("教程", "/guides"), (&guide.h1, &guide.path)]);
    dump(&json!([how_to, article, faq_ld(&guide.faq), trail]))
}

pub fn sitemap(content: &Content, base: &str) -> String {
    let entry = |path: &str, priority: &str, freq: &str| {
        format!(
            "<url><loc>{}</loc><lastmod>{}</lastmod><changefreq>{freq}</changefreq><priority>{priority}</priority></url>",
            absolute(base, path),
            content.updated
        )
    };
    let mut items: Vec<String> = content
        .pages
        .iter()
        .map(|p| entry(&p.path, if p.slug.is_empty() { "1.0" } else { "0.8" }, "weekly"))
        .collect();
    items.push(entry("/guides", "0.7", "weekly"));
    items.extend(content.guides.iter().map(|g| entry(&g.path, "0.7", "monthly")));
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>"#,
        items.concat()
    )
}

pub fn robots(base: &str) -> String {
    format!(
        "User-agent: *\nDisallow: /api/\nDisallow: /video/\nDisallow: /stats\nDisallow: /*?url=\nSitemap: {}\n",
        absolute(base, "/sitemap.xml")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ld_json_cannot_close_the_script_tag() {
        let out = dump(&json!({"a": "</script>"}));
        assert!(!out.contains("</"), "{out}");
        assert!(out.contains(r"\u003c/script>"), "{out}");
    }

    #[test]
    fn sitemap_lists_every_page() {
        let c = Content::embedded().unwrap();
        let xml = sitemap(&c, "https://x.com/");
        assert_eq!(xml.matches("<url>").count(), c.paths().len());
        assert!(xml.contains("<loc>https://x.com/douyin</loc>"));
    }

    #[test]
    fn home_has_website_others_have_breadcrumbs() {
        let c = Content::embedded().unwrap();
        assert!(page_ld(c.home(), "https://x").contains("\"WebSite\""));
        assert!(page_ld(c.page("douyin").unwrap(), "https://x").contains("BreadcrumbList"));
    }
}
