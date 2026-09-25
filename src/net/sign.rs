//! 链接签名：代理 / 下载只处理我们自己解析出来的地址，不做开放代理。
//!
//! 解析结果里每个地址都附一个 HMAC，前端调 /api/proxy 等接口时带回来。

use std::path::Path;

use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// 签名长度（十六进制字符）。128 bit 足够，URL 里短一点好看。
const SIG_HEX_LEN: usize = 32;

#[derive(Clone)]
pub struct Signer {
    key: Vec<u8>,
}

impl std::fmt::Debug for Signer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Signer(..)") // 密钥别进日志
    }
}

impl Signer {
    pub fn new(key: impl Into<Vec<u8>>) -> Self {
        Self { key: key.into() }
    }

    /// 配置里给了就用配置的；否则读 data/secret.key，没有就生成一个。
    ///
    /// 多实例部署时必须显式配成同一个，否则 A 实例签的地址 B 实例不认。
    pub fn load(configured: Option<&str>, data_dir: &Path) -> std::io::Result<Self> {
        if let Some(k) = configured {
            return Ok(Self::new(k.as_bytes()));
        }
        let file = data_dir.join("secret.key");
        if !file.exists() {
            std::fs::create_dir_all(data_dir)?;
            let bytes: [u8; 32] = rand::random();
            std::fs::write(&file, hex::encode(bytes))?;
        }
        Ok(Self::new(std::fs::read_to_string(&file)?.trim().as_bytes()))
    }

    pub fn sign(&self, message: &str) -> String {
        let mut full = hex::encode(self.mac(message.as_bytes()));
        full.truncate(SIG_HEX_LEN);
        full
    }

    /// 常量时间比较。
    pub fn verify(&self, message: &str, sig: &str) -> bool {
        let Ok(given) = hex::decode(sig) else {
            return false;
        };
        if given.len() * 2 != SIG_HEX_LEN {
            return false;
        }
        let mut mac = self.new_mac();
        mac.update(message.as_bytes());
        mac.verify_truncated_left(&given).is_ok()
    }

    /// 完整的 HMAC-SHA256，给统计里 IP 打码之类的场景用。
    pub fn mac(&self, message: &[u8]) -> [u8; 32] {
        let mut mac = self.new_mac();
        mac.update(message);
        mac.finalize().into_bytes().into()
    }

    fn new_mac(&self) -> HmacSha256 {
        // HMAC 接受任意长度的密钥，这里不会失败
        HmacSha256::new_from_slice(&self.key).unwrap_or_else(|_| unreachable!())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_and_tamper() {
        let s = Signer::new(b"k".to_vec());
        let sig = s.sign("https://a/b.mp4");
        assert_eq!(sig.len(), SIG_HEX_LEN);
        assert!(s.verify("https://a/b.mp4", &sig));
        assert!(!s.verify("https://a/c.mp4", &sig));
        assert!(!s.verify("https://a/b.mp4", ""));
        assert!(!s.verify("https://a/b.mp4", "zz"));
        assert!(!Signer::new(b"other".to_vec()).verify("https://a/b.mp4", &sig));
    }

    #[test]
    fn same_key_same_signature_as_python() {
        // Python 版: hmac.new(key, url, sha256).hexdigest()[:32]。线上缓存 / 前端
        // 手里的签名在切换前后都要认，所以算法必须逐字节一致。
        let s = Signer::new(b"secret".to_vec());
        assert_eq!(s.sign("https://example.com/"), "c771eb2410419239cd74ef3f47676e2e");
    }

    #[test]
    fn generated_key_is_reused() {
        let dir = std::env::temp_dir().join(format!("shizhen-sign-{}", rand::random::<u32>()));
        let a = Signer::load(None, &dir).unwrap().sign("x");
        let b = Signer::load(None, &dir).unwrap().sign("x");
        assert_eq!(a, b);
        std::fs::remove_dir_all(&dir).ok();
    }
}
