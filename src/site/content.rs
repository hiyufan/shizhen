//! 落地页和教程的文案，从 content/*.toml 编译进二进制。
//!
//! 改文案只动 TOML，不碰 Rust 代码。这里把 TOML 里的结构转成模板直接能用的形状：
//! 问答、步骤、正文段落都是 `(标题, 内容)` 二元组，模板里写 `for q, a in faq`。

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

type Pair = (String, String);

#[derive(Debug, Deserialize)]
struct Faq {
    q: String,
    a: String,
}

#[derive(Debug, Deserialize)]
struct Section {
    title: String,
    html: String,
}

#[derive(Debug, Deserialize)]
struct Step {
    name: String,
    text: String,
}

#[derive(Debug, Deserialize)]
struct PagesFile {
    updated: String,
    common_faq: HashMap<String, Faq>,
    page: Vec<RawPage>,
}

#[derive(Debug, Deserialize)]
struct RawPage {
    slug: String,
    title: String,
    description: String,
    h1: String,
    lead: String,
    keywords: String,
    placeholder: String,
    nav_label: String,
    common: Vec<String>,
    guides: Vec<String>,
    #[serde(default)]
    faq: Vec<Faq>,
    #[serde(default)]
    body: Vec<Section>,
}

#[derive(Debug, Deserialize)]
struct GuidesFile {
    guide: Vec<RawGuide>,
}

#[derive(Debug, Deserialize)]
struct RawGuide {
    slug: String,
    title: String,
    description: String,
    keywords: String,
    h1: String,
    intro: String,
    tool: String,
    tool_label: String,
    updated: String,
    related: Vec<String>,
    steps: Vec<Step>,
    sections: Vec<Section>,
    faq: Vec<Faq>,
}

/// 一个落地页（slug 为空的是首页）。
#[derive(Debug, Clone, Serialize)]
pub struct Page {
    pub slug: String,
    pub path: String,
    pub title: String,
    pub description: String,
    /// 允许 `<em>` 和 `<br>`，模板里 `| safe`
    pub h1: String,
    pub lead: String,
    pub keywords: String,
    pub placeholder: String,
    pub nav_label: String,
    /// 这页专属的问答在前，挑出来的通用问答在后
    pub all_faq: Vec<Pair>,
    pub body: Vec<Pair>,
    pub guides: Vec<String>,
}

/// 一篇教程。
#[derive(Debug, Clone, Serialize)]
pub struct Guide {
    pub slug: String,
    pub path: String,
    pub title: String,
    pub description: String,
    pub keywords: String,
    pub h1: String,
    pub intro: String,
    pub tool: String,
    pub tool_label: String,
    pub updated: String,
    pub related: Vec<String>,
    pub steps: Vec<Pair>,
    pub sections: Vec<Pair>,
    pub faq: Vec<Pair>,
}

#[derive(Debug)]
pub struct Content {
    pub updated: String,
    pub pages: Vec<Page>,
    pub guides: Vec<Guide>,
}

impl Content {
    /// 编译进来的那一份。TOML 写错了在启动时就报出来，不会等到有人访问那一页。
    pub fn embedded() -> Result<Self, String> {
        Self::parse(include_str!("../../content/pages.toml"), include_str!("../../content/guides.toml"))
    }

    fn parse(pages: &str, guides: &str) -> Result<Self, String> {
        let pages: PagesFile = toml::from_str(pages).map_err(|e| format!("content/pages.toml: {e}"))?;
        let guides: GuidesFile = toml::from_str(guides).map_err(|e| format!("content/guides.toml: {e}"))?;
        let common = &pages.common_faq;
        let content = Self {
            updated: pages.updated,
            pages: pages.page.into_iter().map(|p| page(p, common)).collect::<Result<_, _>>()?,
            guides: guides.guide.into_iter().map(guide).collect(),
        };
        content.check_links()?;
        Ok(content)
    }

    pub fn page(&self, slug: &str) -> Option<&Page> {
        self.pages.iter().find(|p| p.slug == slug)
    }

    pub fn home(&self) -> &Page {
        // check_links 保证了首页存在
        self.page("").unwrap_or(&self.pages[0])
    }

    pub fn guide(&self, slug: &str) -> Option<&Guide> {
        self.guides.iter().find(|g| g.slug == slug)
    }

    pub fn guides_by_slug<'a>(&'a self, slugs: &'a [String]) -> Vec<&'a Guide> {
        slugs.iter().filter_map(|s| self.guide(s)).collect()
    }

    /// 站内所有页面的路径（sitemap、主动推送用）。
    pub fn paths(&self) -> Vec<String> {
        let pages = self.pages.iter().map(|p| p.path.clone());
        let guides = self.guides.iter().map(|g| g.path.clone());
        pages.chain(std::iter::once("/guides".to_owned())).chain(guides).collect()
    }

    /// 互链写错了（指向不存在的教程 / 工具页）在启动时就报错。
    fn check_links(&self) -> Result<(), String> {
        if self.page("").is_none() {
            return Err("content/pages.toml 里没有首页（slug = \"\"）".into());
        }
        let bad_guide = |slug: &String| self.guide(slug).is_none();
        for p in &self.pages {
            if let Some(g) = p.guides.iter().find(|s| bad_guide(s)) {
                return Err(format!("落地页 /{} 引用了不存在的教程 {g}", p.slug));
            }
        }
        for g in &self.guides {
            if let Some(r) = g.related.iter().find(|s| bad_guide(s)) {
                return Err(format!("教程 {} 引用了不存在的教程 {r}", g.slug));
            }
            if self.page(&g.tool).is_none() {
                return Err(format!("教程 {} 指向不存在的工具页 {}", g.slug, g.tool));
            }
        }
        Ok(())
    }
}

fn page(p: RawPage, common: &HashMap<String, Faq>) -> Result<Page, String> {
    let mut all_faq: Vec<Pair> = p.faq.into_iter().map(|f| (f.q, f.a)).collect();
    for key in &p.common {
        let f = common.get(key).ok_or_else(|| format!("落地页 /{} 引用了不存在的通用问答 {key}", p.slug))?;
        all_faq.push((f.q.clone(), f.a.clone()));
    }
    Ok(Page {
        path: if p.slug.is_empty() { "/".into() } else { format!("/{}", p.slug) },
        slug: p.slug,
        title: p.title,
        description: p.description,
        h1: p.h1,
        lead: p.lead,
        keywords: p.keywords,
        placeholder: p.placeholder,
        nav_label: p.nav_label,
        all_faq,
        body: p.body.into_iter().map(|s| (s.title, s.html)).collect(),
        guides: p.guides,
    })
}

fn guide(g: RawGuide) -> Guide {
    Guide {
        path: format!("/guide/{}", g.slug),
        slug: g.slug,
        title: g.title,
        description: g.description,
        keywords: g.keywords,
        h1: g.h1,
        intro: g.intro,
        tool: g.tool,
        tool_label: g.tool_label,
        updated: g.updated,
        related: g.related,
        steps: g.steps.into_iter().map(|s| (s.name, s.text)).collect(),
        sections: g.sections.into_iter().map(|s| (s.title, s.html)).collect(),
        faq: g.faq.into_iter().map(|f| (f.q, f.a)).collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_content_loads_and_links_resolve() {
        let c = Content::embedded().unwrap();
        assert_eq!(c.pages.len(), 9);
        assert_eq!(c.guides.len(), 7);
        assert_eq!(c.home().path, "/");
        assert!(c.page("douyin").is_some());
        assert!(c.home().all_faq.len() >= 7, "首页带全套通用问答");
        assert!(c.paths().contains(&"/guides".to_owned()));
    }

    #[test]
    fn broken_links_fail_fast() {
        let pages = r#"
updated = "x"
[common_faq]
[[page]]
slug = ""
title = "t"
description = "d"
h1 = "h"
lead = "l"
keywords = "k"
placeholder = "p"
nav_label = "n"
common = []
guides = ["nope"]
"#;
        let err = Content::parse(pages, "guide = []").unwrap_err();
        assert!(err.contains("nope"), "{err}");
    }
}
